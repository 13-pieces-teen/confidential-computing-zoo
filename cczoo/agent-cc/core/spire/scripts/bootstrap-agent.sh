#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPIRE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SPIRE_HOME="${SPIRE_HOME:-/opt/spire}"
SERVER_CONFIG="${SERVER_CONFIG:-$SPIRE_DIR/conf/server.conf}"
AGENT_CONFIG="${AGENT_CONFIG:-$SPIRE_DIR/conf/agent.conf}"
SERVER_SOCKET="${SPIRE_SERVER_SOCKET:-/tmp/spire-server/private/api.sock}"
AGENT_SOCKET="${SPIRE_AGENT_SOCKET:-/run/spire/sockets/agent.sock}"

SERVER_BIN="$SPIRE_HOME/bin/spire-server"
AGENT_BIN="$SPIRE_HOME/bin/spire-agent"

if [[ "$EUID" -ne 0 ]]; then
    echo "Run this script as root." >&2
    exit 1
fi

for executable in "$SERVER_BIN" "$AGENT_BIN" openssl python3; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        echo "Required executable not found: $executable" >&2
        exit 1
    fi
done

install -d -m 0755 /var/lib/spire/server /var/lib/spire/agent
install -d -m 0755 /etc/spire /run/spire/sockets /var/log/spire

if [[ ! -f /var/lib/spire/server/upstream-ca.key ]]; then
    openssl ecparam -name prime256v1 -genkey -noout \
        -out /var/lib/spire/server/upstream-ca.key
    openssl req -new -x509 -sha256 -days 3650 \
        -key /var/lib/spire/server/upstream-ca.key \
        -subj "/O=Argus/CN=Argus SPIRE Development Root CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -out /var/lib/spire/server/upstream-ca.crt
    chmod 0600 /var/lib/spire/server/upstream-ca.key
fi

"$SERVER_BIN" validate -config "$SERVER_CONFIG"
"$AGENT_BIN" validate -config "$AGENT_CONFIG"

server_healthy() {
    "$SERVER_BIN" healthcheck -socketPath "$SERVER_SOCKET" >/dev/null 2>&1
}

agent_healthy() {
    "$AGENT_BIN" healthcheck -socketPath "$AGENT_SOCKET" >/dev/null 2>&1
}

wait_for_health() {
    local name="$1"
    local check="$2"

    for _ in {1..30}; do
        if "$check"; then
            return 0
        fi
        sleep 1
    done

    echo "$name did not become healthy; inspect /var/log/spire/${name}.log" >&2
    return 1
}

if ! server_healthy; then
    nohup "$SERVER_BIN" run -config "$SERVER_CONFIG" \
        >/var/log/spire/server.log 2>&1 &
    echo "$!" >/run/spire/server.pid
    wait_for_health server server_healthy
fi

bootstrap_bundle="$(mktemp)"
trap 'rm -f "$bootstrap_bundle"' EXIT
"$SERVER_BIN" bundle show -socketPath "$SERVER_SOCKET" -format pem \
    >"$bootstrap_bundle"
install -m 0644 "$bootstrap_bundle" /etc/spire/bootstrap.crt

if ! agent_healthy; then
    token_json="$($SERVER_BIN token generate -socketPath "$SERVER_SOCKET" -output json)"
    join_token="$(python3 -c 'import json, sys; data=json.load(sys.stdin); print(data.get("value") or data.get("token") or "")' <<<"$token_json")"
    if [[ -z "$join_token" ]]; then
        echo "Unable to parse SPIRE join token." >&2
        exit 1
    fi
    agent_args=(-joinToken "$join_token")

    nohup "$AGENT_BIN" run -config "$AGENT_CONFIG" "${agent_args[@]}" \
        >/var/log/spire/agent.log 2>&1 &
    echo "$!" >/run/spire/agent.pid
    wait_for_health agent agent_healthy
fi

echo "SPIRE Server and Agent are healthy."
"$SERVER_BIN" agent list -socketPath "$SERVER_SOCKET"
