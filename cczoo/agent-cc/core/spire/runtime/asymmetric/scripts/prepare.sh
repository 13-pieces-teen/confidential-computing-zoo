#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPIRE_ROOT="$(cd "$PROFILE_DIR/../.." && pwd)"
AGENT_CC_ROOT="$(cd "$SPIRE_ROOT/../.." && pwd)"
CORE_DIR="$AGENT_CC_ROOT/core"
PLUGIN_MODULE_DIR="$CORE_DIR/spire/plugins/argus-tdx-nodeattestor"
OPENCLAW_DOCKERFILE="$AGENT_CC_ROOT/adapters/OpenClaw/scripts/Dockerfile.sbx"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
GO_CACHE_DIR="${ARGUS_GO_CACHE_DIR:-/tmp/argus-spire-v2-go-cache}"
GO_PROXY="${ARGUS_GO_PROXY:-https://proxy.golang.org,direct}"
TDVM_SPIRE_SERVER_ADDRESS="${V2_TDVM_SPIRE_SERVER_ADDRESS:-10.0.2.2}"
SPIRE_SERVER_PORT="${V2_SPIRE_SERVER_PORT:-18081}"
TDVM_INSTANCE_ID="${V2_TDVM_INSTANCE_ID:-tdvm-v2-0001}"
OPENVIKING_ORIGIN="${V2_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
GUARD_DECISION_TTL_SECONDS="${V2_GUARD_DECISION_TTL_SECONDS:-15}"

fail() {
    printf 'v2 prepare: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$RUNTIME_DIR" =~ ^/[^/]+/[^/]+(/.*)?$ \
    && "$RUNTIME_DIR" != *'//'*
    && "$RUNTIME_DIR" != *'/./'*
    && "$RUNTIME_DIR" != *'/../'*
    && "$RUNTIME_DIR" != */.
    && "$RUNTIME_DIR" != */..
    && "$RUNTIME_DIR" != */ ]] \
    || fail "V2_RUNTIME_DIR must be an unambiguous absolute path at least two levels below /: $RUNTIME_DIR"

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

if [[ "$(id -u)" -ne 0 ]]; then
    fail "run as root so the bind-mounted SPIRE runtime can be owned by uid 1000"
fi

for command_name in docker openssl sed sha256sum awk; do
    require_command "$command_name"
done

[[ "$OPENVIKING_ORIGIN" =~ ^https://[A-Za-z0-9._:-]+$ ]] \
    || fail "V2_OPENVIKING_ORIGIN must be a canonical HTTPS origin: $OPENVIKING_ORIGIN"
[[ "$GUARD_DECISION_TTL_SECONDS" =~ ^[0-9]+$ \
    && "$GUARD_DECISION_TTL_SECONDS" -ge 1 \
    && "$GUARD_DECISION_TTL_SECONDS" -le 300 ]] \
    || fail 'V2_GUARD_DECISION_TTL_SECONDS must be between 1 and 300'

install -d -m 0755 \
    "$RUNTIME_DIR/plugins" \
    "$RUNTIME_DIR/conf" \
    "$RUNTIME_DIR/server-run" \
    "$RUNTIME_DIR/openclaw-agent-run"
install -d -m 0700 \
    "$RUNTIME_DIR/certs" \
    "$RUNTIME_DIR/secrets" \
    "$RUNTIME_DIR/server-data" \
    "$RUNTIME_DIR/openclaw-agent-data"
install -d -m 0755 "$GO_CACHE_DIR"

build_go_binary() {
    local module_dir="$1"
    local package_path="$2"
    local output_name="$3"
    local go_proxy="$4"
    local module_mode="$5"

    docker run --rm \
        -e "GOPROXY=$go_proxy" \
        -e "GOSUMDB=${ARGUS_GOSUMDB:-sum.golang.org}" \
        -e GOMODCACHE=/gomodcache \
        -e CGO_ENABLED=0 \
        -e "HTTPS_PROXY=${HTTPS_PROXY:-}" \
        -e "HTTP_PROXY=${HTTP_PROXY:-}" \
        -e "NO_PROXY=${NO_PROXY:-}" \
        -v "$module_dir:/source:ro" \
        -v "$GO_CACHE_DIR:/gomodcache" \
        -v "$RUNTIME_DIR/plugins:/out" \
        golang:1.24-bookworm sh -ceu "
            mkdir -p /workspace
            cp -a /source/. /workspace/
            cd /workspace
            go build -mod='$module_mode' -trimpath -ldflags='-s -w' \
                -o '/out/$output_name' '$package_path'
        "
}

build_go_binary \
    "$PLUGIN_MODULE_DIR" \
    ./cmd/agent \
    argus-tdx-nodeattestor-agent \
    "$GO_PROXY" \
    readonly
build_go_binary \
    "$PLUGIN_MODULE_DIR" \
    ./cmd/server \
    argus-tdx-nodeattestor-server \
    "$GO_PROXY" \
    readonly
build_go_binary \
    "$PLUGIN_MODULE_DIR" \
    ./cmd/mock-evidence-provider \
    mock-evidence-provider \
    "$GO_PROXY" \
    readonly
build_go_binary \
    "$PLUGIN_MODULE_DIR" \
    ./cmd/mock-trustee \
    mock-trustee \
    "$GO_PROXY" \
    readonly
chmod 0755 "$RUNTIME_DIR/plugins"/*

generate_ca() {
    local name="$1"
    local common_name="$2"
    local key_path="$RUNTIME_DIR/certs/$name-key.pem"
    local cert_path="$RUNTIME_DIR/certs/$name.pem"

    if [[ -f "$key_path" && -f "$cert_path" ]]; then
        return
    fi
    rm -f "$key_path" "$cert_path"
    openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 30 \
        -subj "/O=Argus/CN=$common_name" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -keyout "$key_path" \
        -out "$cert_path" >/dev/null 2>&1
}

issue_certificate() {
    local ca_name="$1"
    local name="$2"
    local common_name="$3"
    local extended_key_usage="$4"
    local subject_alt_name="$5"
    local key_path="$RUNTIME_DIR/certs/$name-key.pem"
    local cert_path="$RUNTIME_DIR/certs/$name.pem"
    local csr_path="$RUNTIME_DIR/certs/$name.csr"
    local ext_path="$RUNTIME_DIR/certs/$name.ext"

    if [[ -f "$key_path" && -f "$cert_path" ]]; then
        return
    fi
    rm -f "$key_path" "$cert_path" "$csr_path" "$ext_path"
    openssl req -newkey rsa:2048 -nodes -sha256 \
        -subj "/O=Argus/CN=$common_name" \
        -keyout "$key_path" \
        -out "$csr_path" >/dev/null 2>&1
    {
        printf '%s\n' \
            'basicConstraints=critical,CA:FALSE' \
            'keyUsage=critical,digitalSignature,keyEncipherment' \
            "extendedKeyUsage=$extended_key_usage" \
            "subjectAltName=$subject_alt_name"
    } >"$ext_path"
    openssl x509 -req -sha256 -days 30 \
        -in "$csr_path" \
        -CA "$RUNTIME_DIR/certs/$ca_name.pem" \
        -CAkey "$RUNTIME_DIR/certs/$ca_name-key.pem" \
        -CAcreateserial \
        -extfile "$ext_path" \
        -out "$cert_path" >/dev/null 2>&1
    rm -f "$csr_path" "$ext_path"
}

generate_ca upstream-ca "Argus v2 SPIRE Upstream CA"
generate_ca trustee-ca "Argus v2 Mock Trustee CA"
generate_ca openclaw-agent-ca "Argus v2 OpenClaw x509pop CA"

issue_certificate \
    trustee-ca \
    trustee-server \
    trustee.argus.local \
    serverAuth \
    "DNS:trustee.argus.local,URI:spiffe://argus.local/service/trustee"
issue_certificate \
    trustee-ca \
    trustee-client \
    "Argus v2 SPIRE Server" \
    clientAuth \
    "URI:spiffe://argus.local/spire/server"
issue_certificate \
    openclaw-agent-ca \
    openclaw-agent \
    "Argus v2 OpenClaw Agent" \
    clientAuth \
    "URI:x509pop://argus.local/role/openclaw"

if [[ ! -s "$RUNTIME_DIR/secrets/guard-api-token" ]]; then
    openssl rand -hex 32 >"$RUNTIME_DIR/secrets/guard-api-token"
fi
cp "$RUNTIME_DIR/secrets/guard-api-token" \
    "$RUNTIME_DIR/secrets/openclaw-guard-api-token"
chown 65532:65532 "$RUNTIME_DIR/secrets/guard-api-token"
chown 1000:1000 "$RUNTIME_DIR/secrets/openclaw-guard-api-token"
chmod 0400 "$RUNTIME_DIR/secrets/guard-api-token" \
    "$RUNTIME_DIR/secrets/openclaw-guard-api-token"

chmod 0600 "$RUNTIME_DIR/certs"/*-key.pem
for cert in "$RUNTIME_DIR"/certs/*.pem; do
    [[ "$cert" == *-key.pem ]] || chmod 0644 "$cert"
done
chown -R 1000:1000 \
    "$RUNTIME_DIR/certs" \
    "$RUNTIME_DIR/server-data" \
    "$RUNTIME_DIR/server-run" \
    "$RUNTIME_DIR/openclaw-agent-data" \
    "$RUNTIME_DIR/openclaw-agent-run"

server_checksum="$(
    sha256sum "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-server" |
        awk '{print $1}'
)"
agent_checksum="$(
    sha256sum "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-agent" |
        awk '{print $1}'
)"

sed \
    "s/__SERVER_CHECKSUM__/$server_checksum/g" \
    "$PROFILE_DIR/config/server.conf.tmpl" \
    >"$RUNTIME_DIR/conf/server.conf"
cp "$PROFILE_DIR/config/openclaw-agent.conf.tmpl" "$RUNTIME_DIR/conf/openclaw-agent.conf"
sed \
    -e "s/__SPIRE_SERVER_ADDRESS__/$TDVM_SPIRE_SERVER_ADDRESS/g" \
    -e "s/__SPIRE_SERVER_PORT__/$SPIRE_SERVER_PORT/g" \
    -e "s/__AGENT_CHECKSUM__/$agent_checksum/g" \
    -e "s/__TDVM_INSTANCE_ID__/$TDVM_INSTANCE_ID/g" \
    "$PROFILE_DIR/config/openviking-agent.conf.tmpl" \
    >"$RUNTIME_DIR/conf/openviking-agent.conf"
cp "$PROFILE_DIR/config/policy.yaml" "$RUNTIME_DIR/conf/policy.yaml"
sed \
    -e "s|__OPENVIKING_ORIGIN__|$OPENVIKING_ORIGIN|g" \
    -e "s|__DECISION_TTL_SECONDS__|$GUARD_DECISION_TTL_SECONDS|g" \
    "$PROFILE_DIR/config/guard-policy.yaml.tmpl" \
    >"$RUNTIME_DIR/conf/guard-policy.yaml"
chmod 0644 "$RUNTIME_DIR/conf"/*

docker build -q \
    -f "$PROFILE_DIR/images/Dockerfile.mock-evidence-provider" \
    -t argus-spire-v2-mock-evidence-provider:local \
    "$RUNTIME_DIR" >/dev/null
docker build -q \
    -f "$PROFILE_DIR/images/Dockerfile.mock-trustee" \
    -t argus-spire-v2-mock-trustee:local \
    "$RUNTIME_DIR" >/dev/null
if [[ "${V2_BUILD_GUARD:-1}" == "1" ]]; then
    docker build -q \
        --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" \
        --build-arg "HTTP_PROXY=${HTTP_PROXY:-}" \
        --build-arg "NO_PROXY=${NO_PROXY:-}" \
        -f "$PROFILE_DIR/images/Dockerfile.guard" \
        -t argus-spire-v2-guard:local \
        "$CORE_DIR" >/dev/null
fi

if [[ "${V2_BUILD_OPENCLAW:-1}" == "1" ]]; then
    docker build -q \
        -f "$OPENCLAW_DOCKERFILE" \
        -t "${V2_OPENCLAW_WORKLOAD_IMAGE:-openclaw-sbx:latest}" \
        "$AGENT_CC_ROOT" >/dev/null
fi

printf '%s\n' \
    "Argus SPIFFE v2 runtime prepared: $RUNTIME_DIR" \
    "OpenClaw NodeAttestor: x509pop" \
    "OpenViking NodeAttestor: argus_tdx" \
    "OpenViking Agent endpoint: $TDVM_SPIRE_SERVER_ADDRESS:$SPIRE_SERVER_PORT" \
    "OpenViking protected origin: $OPENVIKING_ORIGIN" \
    "Agent plugin checksum: $agent_checksum" \
    "Server plugin checksum: $server_checksum" \
    "Next: $SCRIPT_DIR/start-server.sh"
