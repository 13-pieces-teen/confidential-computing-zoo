#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
MODULE_DIR="$REPO_ROOT/core/spire/plugins/argus-tdx-nodeattestor"
RUNTIME_DIR="$SCRIPT_DIR/runtime"
GO_CACHE_DIR="${ARGUS_GO_CACHE_DIR:-/tmp/argus-go-cache}"

mkdir -p "$RUNTIME_DIR"/{plugins,conf,certs,server-data,server-run,agent-data/argus-tdx,agent-run}
chmod 0700 "$RUNTIME_DIR/certs" "$RUNTIME_DIR/server-data" "$RUNTIME_DIR/agent-data" "$RUNTIME_DIR/agent-data/argus-tdx"
chown 1000:1000 "$RUNTIME_DIR/server-data" "$RUNTIME_DIR/server-run"

docker run --rm \
    -e GOPROXY=off -e GOSUMDB=off -e GOMODCACHE=/gomodcache -e CGO_ENABLED=0 \
    -v "$MODULE_DIR:/workspace" -v "$GO_CACHE_DIR:/gomodcache" -v "$RUNTIME_DIR/plugins:/out" \
    -w /workspace golang:1.24-bookworm sh -c '
        go build -mod=readonly -trimpath -o /out/argus-tdx-nodeattestor-agent ./cmd/agent
        go build -mod=readonly -trimpath -o /out/argus-tdx-nodeattestor-server ./cmd/server
        go build -mod=readonly -trimpath -o /out/fake-services ./cmd/fake-services
    '
chmod 0755 "$RUNTIME_DIR/plugins"/*

if [[ ! -f "$RUNTIME_DIR/certs/upstream-ca.pem" ]]; then
    openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 7 \
        -subj "/CN=Argus M3 Upstream CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -keyout "$RUNTIME_DIR/certs/upstream-ca-key.pem" \
        -out "$RUNTIME_DIR/certs/upstream-ca.pem" >/dev/null 2>&1
fi

if [[ ! -f "$RUNTIME_DIR/certs/trustee-ca.pem" ]]; then
    openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 7 \
        -subj "/CN=Argus M3 Trustee CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -keyout "$RUNTIME_DIR/certs/trustee-ca-key.pem" \
        -out "$RUNTIME_DIR/certs/trustee-ca.pem" >/dev/null 2>&1

    openssl req -newkey rsa:2048 -nodes -sha256 \
        -subj "/CN=trustee.argus.local" \
        -keyout "$RUNTIME_DIR/certs/trustee-server-key.pem" \
        -out "$RUNTIME_DIR/certs/trustee-server.csr" >/dev/null 2>&1
    openssl x509 -req -sha256 -days 7 \
        -in "$RUNTIME_DIR/certs/trustee-server.csr" \
        -CA "$RUNTIME_DIR/certs/trustee-ca.pem" \
        -CAkey "$RUNTIME_DIR/certs/trustee-ca-key.pem" -CAcreateserial \
        -extfile <(printf '%s\n' \
            'basicConstraints=critical,CA:FALSE' \
            'keyUsage=critical,digitalSignature,keyEncipherment' \
            'extendedKeyUsage=serverAuth' \
            'subjectAltName=DNS:trustee.argus.local,URI:spiffe://argus.local/service/trustee') \
        -out "$RUNTIME_DIR/certs/trustee-server.pem" >/dev/null 2>&1

    openssl req -newkey rsa:2048 -nodes -sha256 \
        -subj "/CN=SPIRE M3 Server" \
        -keyout "$RUNTIME_DIR/certs/trustee-client-key.pem" \
        -out "$RUNTIME_DIR/certs/trustee-client.csr" >/dev/null 2>&1
    openssl x509 -req -sha256 -days 7 \
        -in "$RUNTIME_DIR/certs/trustee-client.csr" \
        -CA "$RUNTIME_DIR/certs/trustee-ca.pem" \
        -CAkey "$RUNTIME_DIR/certs/trustee-ca-key.pem" -CAcreateserial \
        -extfile <(printf '%s\n' \
            'basicConstraints=critical,CA:FALSE' \
            'keyUsage=critical,digitalSignature,keyEncipherment' \
            'extendedKeyUsage=clientAuth' \
            'subjectAltName=URI:spiffe://argus.local/spire/server') \
        -out "$RUNTIME_DIR/certs/trustee-client.pem" >/dev/null 2>&1
fi

chmod 0600 "$RUNTIME_DIR/certs"/*-key.pem
chmod 0644 "$RUNTIME_DIR/certs"/*.pem
chmod 0600 "$RUNTIME_DIR/certs"/*-key.pem
chown -R 1000:1000 "$RUNTIME_DIR/certs"

server_checksum="$(sha256sum "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-server" | awk '{print $1}')"
agent_checksum="$(sha256sum "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-agent" | awk '{print $1}')"
sed "s/__SERVER_CHECKSUM__/$server_checksum/g" "$SCRIPT_DIR/server.conf.tmpl" > "$RUNTIME_DIR/conf/server.conf"
sed "s/__AGENT_CHECKSUM__/$agent_checksum/g" "$SCRIPT_DIR/agent.conf.tmpl" > "$RUNTIME_DIR/conf/agent.conf"
cp "$SCRIPT_DIR/policy.yaml" "$RUNTIME_DIR/conf/policy.yaml"

docker build -q -f "$SCRIPT_DIR/Dockerfile.fake" -t argus-spire-m3-fake:local "$RUNTIME_DIR" >/dev/null
docker build -q -f "$SCRIPT_DIR/Dockerfile.negative-workload" -t argus-spire-m3-negative-workload:local "$SCRIPT_DIR" >/dev/null
printf 'M3 runtime prepared\nAgent checksum: %s\nServer checksum: %s\n' "$agent_checksum" "$server_checksum"
