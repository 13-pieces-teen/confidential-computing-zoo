#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${DUAL_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
export DUAL_RUNTIME_DIR="$RUNTIME_DIR"
SERVER_SOCKET="/opt/spire/run/server/api.sock"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"
OPENCLAW_CONTAINER="${DUAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENVIKING_CONTAINER="${DUAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
OPENCLAW_PARENT_ID="${DUAL_OPENCLAW_PARENT_ID:-}"
OPENVIKING_PARENT_ID="${DUAL_OPENVIKING_PARENT_ID:-}"
OPENCLAW_TARGET="${DUAL_OPENCLAW_TDVM_SSH_TARGET:-}"
OPENVIKING_TARGET="${DUAL_OPENVIKING_TDVM_SSH_TARGET:-}"
OPENCLAW_RUN="${DUAL_OPENCLAW_GUEST_RUN:-/run/argus-spire-dual/openclaw}"
OPENVIKING_RUN="${DUAL_OPENVIKING_GUEST_RUN:-/run/argus-spire-dual/openviking}"
OPENVIKING_ORIGIN="${DUAL_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
OPENVIKING_HOST_ADDRESS="${DUAL_OPENVIKING_HOST_ADDRESS:-}"
OPENVIKING_PORT="${DUAL_OPENVIKING_PORT:-1943}"

fail() {
    printf 'dual TDVM verification: FAIL: %s\n' "$1" >&2
    exit 1
}

for value in \
    "$OPENCLAW_PARENT_ID" "$OPENVIKING_PARENT_ID" \
    "$OPENCLAW_TARGET" "$OPENVIKING_TARGET" "$OPENVIKING_HOST_ADDRESS"; do
    [[ -n "$value" ]] || fail 'parent IDs, both SSH targets, and DUAL_OPENVIKING_HOST_ADDRESS are required'
done
[[ "$OPENCLAW_PARENT_ID" != "$OPENVIKING_PARENT_ID" ]] \
    || fail 'OpenClaw and OpenViking share one Agent parent'

spire_server() {
    docker compose -f "$COMPOSE_FILE" exec -T spire-server \
        /opt/spire/bin/spire-server "$@" -socketPath "$SERVER_SOCKET"
}

make_ssh_options() {
    local role="$1"
    local -n destination="$2"
    local port identity known_hosts
    if [[ "$role" == openclaw ]]; then
        port="${DUAL_OPENCLAW_TDVM_SSH_PORT:-22}"
        identity="${DUAL_OPENCLAW_TDVM_SSH_IDENTITY:-}"
        known_hosts="${DUAL_OPENCLAW_TDVM_KNOWN_HOSTS:-/tmp/argus-dual-openclaw-known-hosts}"
    else
        port="${DUAL_OPENVIKING_TDVM_SSH_PORT:-22}"
        identity="${DUAL_OPENVIKING_TDVM_SSH_IDENTITY:-}"
        known_hosts="${DUAL_OPENVIKING_TDVM_KNOWN_HOSTS:-/tmp/argus-dual-openviking-known-hosts}"
    fi
    destination=(
        -o BatchMode=yes
        -o ConnectTimeout=10
        -o StrictHostKeyChecking=accept-new
        -o "UserKnownHostsFile=$known_hosts"
        -p "$port"
    )
    # Use an if so the function returns 0 when no identity is configured;
    # a bare "[[ ]] &&" here makes the function exit 1 and, under set -e,
    # silently aborts plain-statement remote_sudo calls.
    if [[ -n "$identity" ]]; then
        destination+=(-i "$identity")
    fi
}

remote_sudo() {
    local role="$1"
    local target="$2"
    shift 2
    local -a options
    local command_string="" argument quoted
    make_ssh_options "$role" options
    for argument in sudo -n "$@"; do
        printf -v quoted '%q' "$argument"
        command_string+="${command_string:+ }$quoted"
    done
    ssh "${options[@]}" "$target" "$command_string"
}

for role in openclaw openviking; do
    if [[ "$role" == openclaw ]]; then
        target="$OPENCLAW_TARGET"
        run_dir="$OPENCLAW_RUN"
        agent_container=argus-dual-openclaw-agent
        workload_container="$OPENCLAW_CONTAINER"
        expected_id="$OPENCLAW_ID"
    else
        target="$OPENVIKING_TARGET"
        run_dir="$OPENVIKING_RUN"
        agent_container=argus-dual-openviking-agent
        workload_container="$OPENVIKING_CONTAINER"
        expected_id="$OPENVIKING_ID"
    fi
    remote_sudo "$role" "$target" test -c /dev/tdx_guest \
        || fail "$role SSH target is not a TD Guest"
    remote_sudo "$role" "$target" test -S "$run_dir/agent.sock" \
        || fail "$role Workload API socket is missing"
    [[ "$(remote_sudo "$role" "$target" /usr/local/bin/docker inspect "$agent_container" --format '{{.State.Running}}')" == true ]] \
        || fail "$role SPIRE Agent is not running"
    [[ "$(remote_sudo "$role" "$target" /usr/local/bin/docker inspect "$workload_container" --format '{{.State.Running}}')" == true ]] \
        || fail "$role workload is not running"
    status_json="$(remote_sudo "$role" "$target" /usr/local/bin/docker exec "$workload_container" cat /run/argus-svid/status.json)"
    grep -Fq "\"spiffe_id\": \"$expected_id\"" <<<"$status_json" \
        || fail "$role workload has the wrong X.509-SVID"
done

entries="$(spire_server entry show -output json)"
printf '%s' "$entries" | python3 -c '
import json
import sys

expected = {
    "spiffe://argus.local/agent/openclaw": sys.argv[1],
    "spiffe://argus.local/service/openviking-cmem": sys.argv[2],
}
observed = {}
for entry in json.load(sys.stdin).get("entries", []):
    identity = entry.get("spiffe_id")
    if isinstance(identity, dict):
        identity = "spiffe://{}{}".format(identity["trust_domain"], identity["path"])
    if identity not in expected:
        continue
    parent = entry.get("parent_id")
    if isinstance(parent, dict):
        parent = "spiffe://{}{}".format(parent["trust_domain"], parent["path"])
    observed[identity] = str(parent)
if observed != expected:
    raise SystemExit("workload registration parents differ: expected={!r} observed={!r}".format(expected, observed))
' "$OPENCLAW_PARENT_ID" "$OPENVIKING_PARENT_ID"

if remote_sudo openclaw "$OPENCLAW_TARGET" \
    curl -kfsS --noproxy '*' --max-time 3 \
    "https://$OPENVIKING_HOST_ADDRESS:$OPENVIKING_PORT/health" >/dev/null 2>&1; then
    fail 'OpenViking accepted TLS without a client X.509-SVID'
fi

remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker exec -i "$OPENVIKING_CONTAINER" python3 - <<'PY'
import socket
import ssl

context = ssl.create_default_context(cafile="/run/argus-svid/bundle.pem")
context.check_hostname = False
context.load_cert_chain("/run/argus-svid/svid.pem", "/run/argus-svid/svid-key.pem")
with socket.create_connection(("127.0.0.1", 1943), timeout=5) as raw:
    with context.wrap_socket(raw, server_hostname="openviking.argus.local") as connection:
        connection.sendall(b"GET /health HTTP/1.1\r\nHost: openviking.argus.local\r\nConnection: close\r\n\r\n")
        if connection.recv(1):
            raise SystemExit("OpenViking accepted its own workload SVID as an OpenClaw client")
PY

remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker exec -i "$OPENCLAW_CONTAINER" node - \
    "$OPENVIKING_ORIGIN" "$OPENCLAW_ID" "$OPENVIKING_ID" <<'NODE'
const fs = require("fs");
const https = require("https");

const origin = process.argv[2];
const callerId = process.argv[3];
const targetId = process.argv[4];

(async () => {
// The caller-local Guard must ALLOW the exact caller/target pair before any
// business request leaves the OpenClaw workload.
const guardToken = fs.readFileSync("/run/secrets/argus_guard_api_token", "utf8").trim();
const guardResponse = await fetch(process.env.ARGUS_GUARD_URL, {
  method: "POST",
  headers: { authorization: `Bearer ${guardToken}`, "content-type": "application/json" },
  body: JSON.stringify({
    request_id: `dual-tdvm-verify-${process.pid}`,
    caller_spiffe_id: callerId,
    target_spiffe_id: targetId,
    target_service: process.env.ARGUS_TARGET_SERVICE,
    target_origin: origin,
    operation: "memory.read",
    data_class: process.env.ARGUS_GUARD_DATA_CLASS,
  }),
  signal: AbortSignal.timeout(5000),
});
if (!guardResponse.ok) throw new Error(`Guard authorize returned HTTP ${guardResponse.status}`);
const guardDecision = await guardResponse.json();
if (guardDecision.decision !== "ALLOW") {
  throw new Error(`Guard denied the request: ${JSON.stringify(guardDecision)}`);
}

// Direct SPIFFE mTLS: present the materialized workload SVID and verify the
// OpenViking server SVID chain plus its exact SPIFFE ID (URI SAN).
const url = new URL(origin);
const tls = {
  key: fs.readFileSync("/run/argus-svid/svid-key.pem"),
  cert: fs.readFileSync("/run/argus-svid/svid.pem"),
  ca: fs.readFileSync("/run/argus-svid/bundle.pem"),
  checkServerIdentity: (_hostname, certificate) => {
    const uris = (certificate.subjectaltname || "")
      .split(/,\s*/)
      .filter((entry) => entry.startsWith("URI:"))
      .map((entry) => entry.slice(4));
    if (!uris.includes(targetId)) {
      return new Error(
        `OpenViking TLS peer SPIFFE ID mismatch: expected ${targetId}, got ${uris.join(",") || "none"}`
      );
    }
  },
};
for (const path of ["/health", "/ready"]) {
  const status = await new Promise((resolve, reject) => {
    const request = https.get(url, { path, servername: url.hostname, timeout: 10000, ...tls }, (response) => {
      response.resume();
      resolve(response.statusCode);
    });
    request.on("timeout", () => request.destroy(new Error(`OpenViking ${path} timed out`)));
    request.on("error", reject);
  });
  if (status !== 200) throw new Error(`OpenViking ${path} returned HTTP ${status}`);
  console.log(`Guard ALLOW -> direct SPIFFE mTLS ${path} -> HTTP ${status}`);
}
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
NODE

printf '%s\n' \
    'Dual-TDVM architecture verification passed.' \
    'OpenClaw and OpenViking use distinct argus_tdx Agent parents and distinct TDVM-local Workload APIs.' \
    'OpenViking rejected missing and wrong client identities.' \
    'OpenClaw Guard authorization followed by direct SPIFFE mTLS requests reached OpenViking.' \
    'Attestation evidence is still mock-stage; this result is not real TDX Quote/QGS acceptance.'
