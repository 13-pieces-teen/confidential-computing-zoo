#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPIRE_ROOT="$(cd "$PROFILE_DIR/../.." && pwd)"
AGENT_CC_ROOT="$(cd "$SPIRE_ROOT/../.." && pwd)"
CORE_DIR="$AGENT_CC_ROOT/core"
NODE_PLUGIN_MODULE_DIR="$CORE_DIR/spire/plugins/argus-tdx-nodeattestor"
WORKLOAD_PLUGIN_MODULE_DIR="$CORE_DIR/spire/plugins/argus-tdx-workloadattestor"
RUNTIME_DIR="${DUAL_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
GO_CACHE_DIR="${ARGUS_GO_CACHE_DIR:-/tmp/argus-spire-dual-go-cache}"
GO_PROXY="${ARGUS_GO_PROXY:-https://proxy.golang.org,direct}"
SPIRE_SERVER_PORT="${DUAL_SPIRE_SERVER_PORT:-18081}"
OPENCLAW_SERVER_ADDRESS="${DUAL_OPENCLAW_SPIRE_SERVER_ADDRESS:-10.0.2.2}"
OPENVIKING_SERVER_ADDRESS="${DUAL_OPENVIKING_SPIRE_SERVER_ADDRESS:-10.0.2.2}"
OPENVIKING_TRUSTEE_ADDRESS="${DUAL_OPENVIKING_TRUSTEE_ADDRESS:-$OPENVIKING_SERVER_ADDRESS}"
TRUSTEE_PORT="${DUAL_TDVM_TRUSTEE_PORT:-18443}"
OPENCLAW_INSTANCE_ID="${DUAL_OPENCLAW_TDVM_INSTANCE_ID:-tdvm-openclaw-0001}"
OPENVIKING_INSTANCE_ID="${DUAL_OPENVIKING_TDVM_INSTANCE_ID:-tdvm-openviking-0001}"
OPENVIKING_ORIGIN="${DUAL_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
GUARD_DECISION_TTL_SECONDS="${DUAL_GUARD_DECISION_TTL_SECONDS:-15}"
OPENCLAW_IMAGE="${DUAL_OPENCLAW_WORKLOAD_IMAGE:-argus-dual-openclaw:local}"
OPENCLAW_SANDBOX_IMAGE="${DUAL_OPENCLAW_SANDBOX_IMAGE:-openclaw-sandbox:bookworm-slim}"
OPENCLAW_BROKER_IMAGE="${DUAL_OPENCLAW_BROKER_IMAGE:-argus-openclaw-egress-sidecar:local}"
OPENVIKING_IMAGE="${DUAL_OPENVIKING_WORKLOAD_IMAGE:-argus-dual-openviking:v0.4.8}"
OPENVIKING_BROKER_IMAGE="${DUAL_OPENVIKING_BROKER_IMAGE:-argus-openviking-broker-sidecar:local}"
OPENVIKING_BASE="${DUAL_OPENVIKING_BASE:-ghcr.io/volcengine/openviking@sha256:27d3c97bddbe81f31d2c5af1f31e9d504b5928506c88f559a23faf86358169b7}"
SPIRE_AGENT_IMAGE="${DUAL_SPIRE_AGENT_IMAGE:-ghcr.io/spiffe/spire-agent:1.15.2}"

fail() {
    printf 'dual TDVM prepare: FAIL: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

[[ "$RUNTIME_DIR" =~ ^/[^/]+/[^/]+(/.*)?$ \
    && "$RUNTIME_DIR" != *'//'* \
    && "$RUNTIME_DIR" != *'/./'* \
    && "$RUNTIME_DIR" != *'/../'* \
    && "$RUNTIME_DIR" != */. \
    && "$RUNTIME_DIR" != */.. \
    && "$RUNTIME_DIR" != */ ]] \
    || fail "DUAL_RUNTIME_DIR must be an unambiguous absolute path at least two levels below /: $RUNTIME_DIR"
[[ "$OPENCLAW_INSTANCE_ID" =~ ^[a-z0-9][a-z0-9_-]{0,127}$ ]] \
    || fail "invalid OpenClaw TDVM instance ID: $OPENCLAW_INSTANCE_ID"
[[ "$OPENVIKING_INSTANCE_ID" =~ ^[a-z0-9][a-z0-9_-]{0,127}$ ]] \
    || fail "invalid OpenViking TDVM instance ID: $OPENVIKING_INSTANCE_ID"
[[ "$OPENCLAW_INSTANCE_ID" != "$OPENVIKING_INSTANCE_ID" ]] \
    || fail 'OpenClaw and OpenViking TDVM instance IDs must differ'
[[ "$OPENVIKING_ORIGIN" =~ ^https://[A-Za-z0-9._:-]+$ ]] \
    || fail "DUAL_OPENVIKING_ORIGIN must be a canonical HTTPS origin: $OPENVIKING_ORIGIN"
[[ "$GUARD_DECISION_TTL_SECONDS" =~ ^[0-9]+$ \
    && "$GUARD_DECISION_TTL_SECONDS" -ge 1 \
    && "$GUARD_DECISION_TTL_SECONDS" -le 300 ]] \
    || fail 'DUAL_GUARD_DECISION_TTL_SECONDS must be between 1 and 300'
[[ "$OPENVIKING_BASE" == *@sha256:* ]] \
    || fail 'DUAL_OPENVIKING_BASE must use an immutable @sha256 digest'

if [[ "$(id -u)" -ne 0 ]]; then
    fail 'run as root so generated SPIRE runtime data can be owned by uid 1000'
fi
for command_name in docker openssl sed sha256sum awk; do
    require_command "$command_name"
done

install -d -m 0755 \
    "$RUNTIME_DIR/plugins" \
    "$RUNTIME_DIR/conf" \
    "$RUNTIME_DIR/server-run"
install -d -m 0700 \
    "$RUNTIME_DIR/certs" \
    "$RUNTIME_DIR/secrets" \
    "$RUNTIME_DIR/server-data"
install -d -m 0755 "$GO_CACHE_DIR"

download_go_dependencies() {
    local module_dir="$1"

    docker run --rm \
        -e "GOPROXY=$GO_PROXY" \
        -e "GOSUMDB=${ARGUS_GOSUMDB:-sum.golang.org}" \
        -e GOMODCACHE=/gomodcache \
        -e GOFLAGS=-mod=readonly \
        -e "HTTPS_PROXY=${HTTPS_PROXY:-}" \
        -e "HTTP_PROXY=${HTTP_PROXY:-}" \
        -e "NO_PROXY=${NO_PROXY:-}" \
        -v "$module_dir:/workspace:ro" \
        -v "$GO_CACHE_DIR:/gomodcache" \
        -w /workspace \
        golang:1.24-bookworm go mod download
}

build_go_binary() {
    local module_dir="$1"
    local package_path="$2"
    local output_name="$3"

    docker run --rm \
        -e GOPROXY=off \
        -e GOSUMDB=off \
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
            go build -mod=readonly -trimpath -ldflags='-s -w' \\
                -o '/out/$output_name' '$package_path'
        "
}

download_go_dependencies "$NODE_PLUGIN_MODULE_DIR"
download_go_dependencies "$WORKLOAD_PLUGIN_MODULE_DIR"
build_go_binary "$NODE_PLUGIN_MODULE_DIR" ./cmd/agent argus-tdx-nodeattestor-agent
build_go_binary "$NODE_PLUGIN_MODULE_DIR" ./cmd/server argus-tdx-nodeattestor-server
build_go_binary "$NODE_PLUGIN_MODULE_DIR" ./cmd/mock-evidence-provider mock-evidence-provider
build_go_binary "$NODE_PLUGIN_MODULE_DIR" ./cmd/mock-trustee mock-trustee
build_go_binary "$WORKLOAD_PLUGIN_MODULE_DIR" \
    ./cmd/argus-tdx-workloadattestor argus-tdx-workloadattestor
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

generate_ca upstream-ca 'Argus dual TDVM SPIRE Upstream CA'
generate_ca trustee-ca 'Argus dual TDVM Mock Trustee CA'
issue_certificate \
    trustee-ca trustee-server trustee.argus.local serverAuth \
    'DNS:trustee.argus.local,URI:spiffe://argus.local/service/trustee'
issue_certificate \
    trustee-ca trustee-client 'Argus dual TDVM SPIRE Server' clientAuth \
    'URI:spiffe://argus.local/spire/server'

if [[ ! -s "$RUNTIME_DIR/secrets/guard-api-token" ]]; then
    openssl rand -hex 32 >"$RUNTIME_DIR/secrets/guard-api-token"
fi
cp "$RUNTIME_DIR/secrets/guard-api-token" \
    "$RUNTIME_DIR/secrets/openclaw-guard-api-token"
if [[ ! -s "$RUNTIME_DIR/secrets/openclaw-gateway-token" ]]; then
    openssl rand -hex 32 >"$RUNTIME_DIR/secrets/openclaw-gateway-token"
fi

chmod 0600 "$RUNTIME_DIR/certs"/*-key.pem
for cert in "$RUNTIME_DIR"/certs/*.pem; do
    [[ "$cert" == *-key.pem ]] || chmod 0644 "$cert"
done
chmod 0400 "$RUNTIME_DIR/secrets"/*
chown 65532:65532 "$RUNTIME_DIR/secrets/guard-api-token"
chown 1000:1000 "$RUNTIME_DIR/secrets/openclaw-guard-api-token"
chown -R 1000:1000 \
    "$RUNTIME_DIR/certs" \
    "$RUNTIME_DIR/server-data" \
    "$RUNTIME_DIR/server-run"

server_checksum="$(sha256sum "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-server" | awk '{print $1}')"
agent_checksum="$(sha256sum "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-agent" | awk '{print $1}')"
workload_attestor_checksum="$(sha256sum "$RUNTIME_DIR/plugins/argus-tdx-workloadattestor" | awk '{print $1}')"

sed "s/__SERVER_CHECKSUM__/$server_checksum/g" \
    "$PROFILE_DIR/config/server.conf.tmpl" \
    >"$RUNTIME_DIR/conf/server.conf"

render_agent_config() {
    local role="$1"
    local server_address="$2"
    local instance_id="$3"
    local template="$PROFILE_DIR/config/$role-agent.conf.tmpl"

    sed \
        -e "s/__SPIRE_SERVER_ADDRESS__/$server_address/g" \
        -e "s/__SPIRE_SERVER_PORT__/$SPIRE_SERVER_PORT/g" \
        -e "s/__AGENT_CHECKSUM__/$agent_checksum/g" \
        -e "s/__WORKLOAD_ATTESTOR_CHECKSUM__/$workload_attestor_checksum/g" \
        -e "s/__TDVM_INSTANCE_ID__/$instance_id/g" \
        -e "s/__TRUSTEE_ADDRESS__/$OPENVIKING_TRUSTEE_ADDRESS/g" \
        -e "s/__TRUSTEE_PORT__/$TRUSTEE_PORT/g" \
        "$template" \
        >"$RUNTIME_DIR/conf/$role-agent.conf"
}

render_agent_config openclaw "$OPENCLAW_SERVER_ADDRESS" "$OPENCLAW_INSTANCE_ID"
render_agent_config openviking "$OPENVIKING_SERVER_ADDRESS" "$OPENVIKING_INSTANCE_ID"
cp "$PROFILE_DIR/config/policy.yaml" "$RUNTIME_DIR/conf/policy.yaml"
sed \
    -e "s|__OPENVIKING_ORIGIN__|$OPENVIKING_ORIGIN|g" \
    -e "s|__DECISION_TTL_SECONDS__|$GUARD_DECISION_TTL_SECONDS|g" \
    "$PROFILE_DIR/config/guard-policy.yaml.tmpl" \
    >"$RUNTIME_DIR/conf/guard-policy.yaml"
chmod 0644 "$RUNTIME_DIR/conf"/*

docker build -q \
    -f "$PROFILE_DIR/images/Dockerfile.mock-evidence-provider" \
    -t argus-spire-dual-mock-evidence-provider:local \
    "$RUNTIME_DIR" >/dev/null
docker build -q \
    -f "$PROFILE_DIR/images/Dockerfile.mock-trustee" \
    -t argus-spire-dual-mock-trustee:local \
    "$RUNTIME_DIR" >/dev/null
docker pull "$SPIRE_AGENT_IMAGE" >/dev/null

if [[ "${DUAL_BUILD_OPENVIKING_BROKER:-1}" == "1" ]]; then
    docker build -q \
        --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" \
        --build-arg "HTTP_PROXY=${HTTP_PROXY:-}" \
        --build-arg "NO_PROXY=${NO_PROXY:-}" \
        -f "$AGENT_CC_ROOT/adapters/OpenViking/configs/Dockerfile.broker-sidecar" \
        -t "$OPENVIKING_BROKER_IMAGE" \
        "$AGENT_CC_ROOT" >/dev/null
fi

if [[ "${DUAL_BUILD_OPENCLAW_BROKER:-1}" == "1" ]]; then
    docker build -q \
        --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" \
        --build-arg "HTTP_PROXY=${HTTP_PROXY:-}" \
        --build-arg "NO_PROXY=${NO_PROXY:-}" \
        -f "$AGENT_CC_ROOT/adapters/OpenClaw/scripts/Dockerfile.egress-sidecar" \
        -t "$OPENCLAW_BROKER_IMAGE" \
        "$AGENT_CC_ROOT" >/dev/null
fi

if [[ "${DUAL_BUILD_GUARD:-1}" == "1" ]]; then
    docker build -q \
        --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" \
        --build-arg "HTTP_PROXY=${HTTP_PROXY:-}" \
        --build-arg "NO_PROXY=${NO_PROXY:-}" \
        -f "$PROFILE_DIR/images/Dockerfile.guard" \
        -t argus-spire-dual-guard:local \
        "$CORE_DIR" >/dev/null
fi
if [[ "${DUAL_BUILD_OPENCLAW:-1}" == "1" ]]; then
    docker build -q \
        -f "$AGENT_CC_ROOT/adapters/OpenClaw/scripts/Dockerfile.sbx-runtime" \
        -t "$OPENCLAW_IMAGE" \
        "$AGENT_CC_ROOT" >/dev/null
    docker build -q \
        -f "$AGENT_CC_ROOT/adapters/OpenClaw/scripts/Dockerfile.sandbox" \
        -t "$OPENCLAW_SANDBOX_IMAGE" \
        "$AGENT_CC_ROOT/adapters/OpenClaw/scripts" >/dev/null
fi
if [[ "${DUAL_BUILD_OPENVIKING:-1}" == "1" ]]; then
    docker build -q \
        --build-arg "OPENVIKING_BASE=$OPENVIKING_BASE" \
        -f "$AGENT_CC_ROOT/adapters/OpenViking/configs/Dockerfile.openviking" \
        -t "$OPENVIKING_IMAGE" \
        "$AGENT_CC_ROOT" >/dev/null
fi

printf '%s\n' \
    "Dual TDVM runtime prepared: $RUNTIME_DIR" \
    "OpenClaw instance: $OPENCLAW_INSTANCE_ID" \
    "OpenViking instance: $OPENVIKING_INSTANCE_ID" \
    "OpenViking origin: $OPENVIKING_ORIGIN" \
    "OpenClaw Egress Broker image: $OPENCLAW_BROKER_IMAGE" \
    "OpenViking Broker image: $OPENVIKING_BROKER_IMAGE" \
    'Both workload Agents use argus_tdx and expose Broker API only to their local Broker Sidecar.' \
    'Evidence Provider and Trustee images are mock-stage only.'
