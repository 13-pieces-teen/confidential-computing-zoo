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
BROKER_ID="spiffe://argus.local/infra/openviking-broker"
OPENCLAW_CONTAINER="${DUAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENVIKING_CONTAINER="${DUAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
BROKER_CONTAINER="${DUAL_OPENVIKING_BROKER_CONTAINER:-agentcc-openviking-broker-sidecar}"
BROKER_IMAGE="${DUAL_OPENVIKING_BROKER_IMAGE:-argus-openviking-broker-sidecar:local}"
WRONG_CLIENT_CONTAINER="argus-dual-openviking-wrong-client-sidecar"
OPENCLAW_PARENT_ID="${DUAL_OPENCLAW_PARENT_ID:-}"
OPENVIKING_PARENT_ID="${DUAL_OPENVIKING_PARENT_ID:-}"
OPENCLAW_TARGET="${DUAL_OPENCLAW_TDVM_SSH_TARGET:-}"
OPENVIKING_TARGET="${DUAL_OPENVIKING_TDVM_SSH_TARGET:-}"
OPENCLAW_RUN="${DUAL_OPENCLAW_GUEST_RUN:-/run/argus-spire-dual/openclaw}"
OPENVIKING_RUN="${DUAL_OPENVIKING_GUEST_RUN:-/run/argus-spire-dual/openviking}"
OPENVIKING_BROKER_RUN="${DUAL_OPENVIKING_GUEST_BROKER_RUN:-/run/argus-spire-dual/openviking-broker}"
OPENVIKING_ORIGIN="${DUAL_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
OPENVIKING_HOST_ADDRESS="${DUAL_OPENVIKING_HOST_ADDRESS:-}"
OPENVIKING_PORT="${DUAL_OPENVIKING_PORT:-1943}"
TRUSTEE_PORT="${DUAL_TDVM_TRUSTEE_PORT:-18443}"
EXPECTED_DECISION="${DUAL_EXPECT_WORKLOAD_DECISION:-allow}"
VERIFY_TARGET_EXIT="${DUAL_VERIFY_TARGET_EXIT:-1}"

fail() {
    printf 'dual TDVM verification: FAIL: %s\n' "$1" >&2
    exit 1
}

for value in \
    "$OPENCLAW_PARENT_ID" "$OPENVIKING_PARENT_ID" \
    "$OPENCLAW_TARGET" "$OPENVIKING_TARGET"; do
    [[ -n "$value" ]] || fail 'parent IDs and both SSH targets are required'
done
[[ "$OPENCLAW_PARENT_ID" != "$OPENVIKING_PARENT_ID" ]] \
    || fail 'OpenClaw and OpenViking share one Agent parent'
[[ "$EXPECTED_DECISION" == allow || "$EXPECTED_DECISION" == deny ]] \
    || fail 'DUAL_EXPECT_WORKLOAD_DECISION must be allow or deny'
if [[ "$EXPECTED_DECISION" == allow ]]; then
    [[ -n "$OPENVIKING_HOST_ADDRESS" ]] \
        || fail 'DUAL_OPENVIKING_HOST_ADDRESS is required for ALLOW verification'
fi

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

container_running() {
    remote_sudo "$1" "$2" /usr/local/bin/docker inspect "$3" \
        --format '{{.State.Running}}' 2>/dev/null
}

for role in openclaw openviking; do
    if [[ "$role" == openclaw ]]; then
        target="$OPENCLAW_TARGET"
        run_dir="$OPENCLAW_RUN"
        provider_container=argus-dual-openclaw-evidence
        agent_container=argus-dual-openclaw-agent
    else
        target="$OPENVIKING_TARGET"
        run_dir="$OPENVIKING_RUN"
        provider_container=argus-dual-openviking-evidence
        agent_container=argus-dual-openviking-agent
    fi
    remote_sudo "$role" "$target" test -c /dev/tdx_guest \
        || fail "$role SSH target is not a TD Guest"
    remote_sudo "$role" "$target" test -S "$run_dir/agent.sock" \
        || fail "$role Workload API socket is missing"
    [[ "$(container_running "$role" "$target" "$provider_container")" == true ]] \
        || fail "$role mock Evidence Provider is not running"
    [[ "$(container_running "$role" "$target" "$agent_container")" == true ]] \
        || fail "$role SPIRE Agent is not running"
done

agents="$(spire_server agent list -output json)"
printf '%s' "$agents" | python3 -c '
import json
import sys
import time

expected = set(sys.argv[1:])
observed = set()
for agent in json.load(sys.stdin).get("agents", []):
    if int(agent.get("x509svid_expires_at", 0)) <= time.time():
        continue
    identity = agent.get("id")
    if isinstance(identity, dict):
        identity = "spiffe://{}{}".format(identity["trust_domain"], identity["path"])
    observed.add(str(identity))
missing = expected - observed
if missing:
    raise SystemExit("configured Agent parents are not live: {}".format(", ".join(sorted(missing))))
' "$OPENCLAW_PARENT_ID" "$OPENVIKING_PARENT_ID"

entries="$(spire_server entry show -output json)"
printf '%s' "$entries" | python3 -c '
import json
import sys

expected_parents = {
    "spiffe://argus.local/agent/openclaw": sys.argv[1],
    "spiffe://argus.local/service/openviking-cmem": sys.argv[2],
    "spiffe://argus.local/infra/openviking-broker": sys.argv[2],
}
matched = {identity: [] for identity in expected_parents}
for entry in json.load(sys.stdin).get("entries", []):
    identity = entry.get("spiffe_id")
    if isinstance(identity, dict):
        identity = "spiffe://{}{}".format(identity["trust_domain"], identity["path"])
    if identity in matched:
        matched[identity].append(entry)
for identity, values in matched.items():
    if len(values) != 1:
        raise SystemExit("expected exactly one registration entry for {}; found {}".format(identity, len(values)))

selectors = {}
attributes = {}
for identity, values in matched.items():
    entry = values[0]
    parent = entry.get("parent_id")
    if isinstance(parent, dict):
        parent = "spiffe://{}{}".format(parent["trust_domain"], parent["path"])
    if str(parent) != expected_parents[identity]:
        raise SystemExit("{} has parent {}, expected {}".format(identity, parent, expected_parents[identity]))
    selectors[identity] = {
        "{}:{}".format(selector.get("type"), selector.get("value"))
        for selector in entry.get("selectors", [])
    }
    attributes[identity] = entry.get("additional_attributes") or {}

openclaw = selectors["spiffe://argus.local/agent/openclaw"]
target = selectors["spiffe://argus.local/service/openviking-cmem"]
broker = selectors["spiffe://argus.local/infra/openviking-broker"]
if "docker:label:argus.workload:openclaw" not in openclaw:
    raise SystemExit("OpenClaw entry is missing its workload label selector")
required_target = {
    "docker:label:argus.workload:openviking-cmem",
    "argus_tdx_workload:verified:true",
    "argus_tdx_workload:workload_id:openviking-cmem",
    "argus_tdx_workload:policy:openviking-cmem-v1",
}
missing = required_target - target
if missing:
    raise SystemExit("OpenViking target entry is missing selectors: {}".format(sorted(missing)))
if not attributes["spiffe://argus.local/service/openviking-cmem"].get("disable_x509_svid_prefetch", False):
    raise SystemExit("OpenViking target entry does not disable X.509-SVID prefetch")
if "docker:label:argus.component:openviking-broker" not in broker:
    raise SystemExit("Broker entry is missing its dedicated Docker label selector")
for name, values in (("OpenClaw", openclaw), ("OpenViking target", target), ("Broker", broker)):
    for prefix in ("docker:image_id:", "docker:image_config_digest:sha256:"):
        if not any(value.startswith(prefix) for value in values):
            raise SystemExit("{} entry is missing a {} selector".format(name, prefix))
' "$OPENCLAW_PARENT_ID" "$OPENVIKING_PARENT_ID"

[[ "$(container_running openviking "$OPENVIKING_TARGET" "$OPENVIKING_CONTAINER")" == true ]] \
    || fail 'TC-API OpenViking container is not running'
openviking_mounts="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" \
    --format '{{range .Mounts}}{{println .Destination}}{{end}}')"
if grep -Eq '^(/opt/spire|/run/spire|/run/argus-svid)(/|$)' <<<"$openviking_mounts"; then
    fail 'unmodified OpenViking directly mounts SPIRE identity material'
fi
openviking_env="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" \
    --format '{{range .Config.Env}}{{println .}}{{end}}')"
if grep -Eq '^(SPIFFE_ENDPOINT_SOCKET|ARGUS_SPIFFE_ENABLED|ARGUS_WORKLOAD_SPIFFE_ID)=' <<<"$openviking_env"; then
    fail 'unmodified OpenViking is configured to obtain SPIFFE identity directly'
fi
openviking_plaintext_binding="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker port "$OPENVIKING_CONTAINER" 1933/tcp)"
[[ "$openviking_plaintext_binding" == '127.0.0.1:1933' ]] \
    || fail "OpenViking plaintext binding is $openviking_plaintext_binding, expected 127.0.0.1:1933"

remote_sudo openviking "$OPENVIKING_TARGET" test -S "$OPENVIKING_BROKER_RUN/broker.sock" \
    || fail 'OpenViking Broker API socket is missing'
broker_socket_stat="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    stat -c '%u:%g %a' "$OPENVIKING_BROKER_RUN/broker.sock")"
[[ "$broker_socket_stat" == '0:1000 770' ]] \
    || fail "Broker API socket permissions are $broker_socket_stat, expected 0:1000 770"
openviking_agent_user="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect argus-dual-openviking-agent --format '{{.Config.User}}')"
[[ "$openviking_agent_user" == '0:0' ]] \
    || fail "OpenViking SPIRE Agent user is $openviking_agent_user, expected 0:0"
broker_user="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$BROKER_CONTAINER" --format '{{.Config.User}}')"
[[ "$broker_user" == '1000:1000' ]] \
    || fail "Broker Sidecar user is $broker_user, expected 1000:1000"

target_pid="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.State.Pid}}')"
target_container_id="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.Id}}')"
broker_command="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$BROKER_CONTAINER" --format '{{json .Config.Cmd}}')"
[[ "$broker_command" == *"-target-pid=$target_pid"* ]] \
    || fail "Broker Sidecar does not reference the current OpenViking host PID $target_pid"
broker_workload_mount="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$BROKER_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/opt/spire/run/agent"}}{{.Source}}{{end}}{{end}}')"
broker_api_mount="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$BROKER_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/opt/spire/run/broker"}}{{.Source}}{{end}}{{end}}')"
[[ "$broker_workload_mount" == "$OPENVIKING_RUN" ]] \
    || fail "Broker Workload API mount is $broker_workload_mount, expected $OPENVIKING_RUN"
[[ "$broker_api_mount" == "$OPENVIKING_BROKER_RUN" ]] \
    || fail "Broker API mount is $broker_api_mount, expected $OPENVIKING_BROKER_RUN"

broker_logs="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker logs "$BROKER_CONTAINER" 2>&1)"
trustee_metrics="$(curl -fsS \
    --noproxy '*' \
    --cacert "$RUNTIME_DIR/certs/trustee-ca.pem" \
    --cert "$RUNTIME_DIR/certs/trustee-client.pem" \
    --key "$RUNTIME_DIR/certs/trustee-client-key.pem" \
    --resolve "trustee.argus.local:$TRUSTEE_PORT:127.0.0.1" \
    "https://trustee.argus.local:$TRUSTEE_PORT/metrics")"

if [[ "$EXPECTED_DECISION" == deny ]]; then
    [[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == false ]] \
        || fail 'Broker Sidecar did not stop after the Trustee DENY'
    ! grep -Fq 'OpenViking mTLS listener is ready' <<<"$broker_logs" \
        || fail 'Broker Sidecar listened on 1943 while Mock Trustee decision was DENY'
    grep -Fq 'Broker subscription denied' <<<"$broker_logs" \
        || fail 'Broker Sidecar did not report the expected Broker subscription denial'
    denied_metric="$(grep -E '^argus_m4_fake_requests_total\{service="workload_trustee",result="denied"\} [1-9][0-9]*$' <<<"$trustee_metrics" || true)"
    [[ -n "$denied_metric" ]] \
        || fail 'Mock Trustee did not record a denied workload verification request'
    printf '%s\n' \
        'Dual-TDVM DENY verification passed.' \
        "OpenViking container: $target_container_id (PID $target_pid, still running)" \
        "Broker socket: $broker_socket_stat" \
        "Trustee metric: $denied_metric" \
        'The Sidecar received no target SVID and never listened on 1943.' \
        'Evidence Provider and Trustee are still mock-stage.'
    exit 0
fi

[[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == true ]] \
    || fail 'Broker Sidecar is not running after Trustee ALLOW'
grep -Fq 'OpenViking mTLS listener is ready' <<<"$broker_logs" \
    || fail 'Broker Sidecar did not receive the strongly selected target SVID'
allowed_metric="$(grep -E '^argus_m4_fake_requests_total\{service="workload_trustee",result="ok"\} [1-9][0-9]*$' <<<"$trustee_metrics" || true)"
[[ -n "$allowed_metric" ]] \
    || fail 'Mock Trustee did not record an allowed workload verification request'
[[ "$(container_running openclaw "$OPENCLAW_TARGET" "$OPENCLAW_CONTAINER")" == true ]] \
    || fail 'OpenClaw workload is not running'
openclaw_status="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker exec "$OPENCLAW_CONTAINER" cat /run/argus-svid/status.json)"
grep -Fq "\"spiffe_id\": \"$OPENCLAW_ID\"" <<<"$openclaw_status" \
    || fail 'OpenClaw workload has the wrong X.509-SVID'

if remote_sudo openclaw "$OPENCLAW_TARGET" \
    curl -kfsS --noproxy '*' --max-time 3 \
    "https://$OPENVIKING_HOST_ADDRESS:$OPENVIKING_PORT/health" >/dev/null 2>&1; then
    fail 'Broker Sidecar accepted TLS without a client X.509-SVID'
fi
if remote_sudo openclaw "$OPENCLAW_TARGET" \
    curl -fsS --noproxy '*' --max-time 3 \
    "http://$OPENVIKING_HOST_ADDRESS:1933/health" >/dev/null 2>&1; then
    fail 'OpenClaw TDVM can reach OpenViking plaintext port 1933'
fi

remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker exec -i "$OPENCLAW_CONTAINER" node - \
    "$OPENVIKING_ORIGIN" "$OPENCLAW_ID" "$OPENVIKING_ID" <<'NODE'
const fs = require("fs");
const https = require("https");

const origin = process.argv[2];
const callerId = process.argv[3];
const targetId = process.argv[4];

(async () => {
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
        return new Error(`OpenViking TLS peer SPIFFE ID mismatch: expected ${targetId}, got ${uris.join(",") || "none"}`);
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

cleanup_wrong_client() {
    remote_sudo openviking "$OPENVIKING_TARGET" \
        /usr/local/bin/docker rm -f "$WRONG_CLIENT_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup_wrong_client EXIT
ready_count_before="$(grep -Fc 'OpenViking mTLS listener is ready' <<<"$broker_logs")"
remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker stop "$BROKER_CONTAINER" >/dev/null
remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker run -d \
    --name "$WRONG_CLIENT_CONTAINER" \
    --network host \
    --pid host \
    --label argus.component=openviking-broker \
    --volume "$OPENVIKING_RUN:/opt/spire/run/agent:ro" \
    --volume "$OPENVIKING_BROKER_RUN:/opt/spire/run/broker:ro" \
    "$BROKER_IMAGE" \
    -workload-api=unix:///opt/spire/run/agent/agent.sock \
    -broker-socket=/opt/spire/run/broker/broker.sock \
    "-broker-spiffe-id=$BROKER_ID" \
    "-agent-spiffe-id=$OPENVIKING_PARENT_ID" \
    "-target-spiffe-id=$OPENVIKING_ID" \
    -client-spiffe-id=spiffe://argus.local/agent/not-openclaw \
    "-target-pid=$target_pid" \
    "-listen=0.0.0.0:$OPENVIKING_PORT" \
    -upstream=http://127.0.0.1:1933 >/dev/null
wrong_ready=0
for _ in $(seq 1 30); do
    wrong_logs="$(remote_sudo openviking "$OPENVIKING_TARGET" \
        /usr/local/bin/docker logs "$WRONG_CLIENT_CONTAINER" 2>&1)"
    if grep -Fq 'OpenViking mTLS listener is ready' <<<"$wrong_logs"; then
        wrong_ready=1
        break
    fi
    [[ "$(container_running openviking "$OPENVIKING_TARGET" "$WRONG_CLIENT_CONTAINER")" == true ]] || break
    sleep 1
done
[[ "$wrong_ready" == 1 ]] || fail 'temporary wrong-client Sidecar did not become ready'

remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker exec -i "$OPENCLAW_CONTAINER" node - \
    "$OPENVIKING_ORIGIN" "$OPENVIKING_ID" <<'NODE'
const fs = require("fs");
const https = require("https");
const url = new URL(process.argv[2]);
const targetId = process.argv[3];
const request = https.get(url, {
  path: "/health",
  servername: url.hostname,
  timeout: 5000,
  key: fs.readFileSync("/run/argus-svid/svid-key.pem"),
  cert: fs.readFileSync("/run/argus-svid/svid.pem"),
  ca: fs.readFileSync("/run/argus-svid/bundle.pem"),
  checkServerIdentity: (_hostname, certificate) => {
    const uris = (certificate.subjectaltname || "")
      .split(/,\s*/)
      .filter((entry) => entry.startsWith("URI:"))
      .map((entry) => entry.slice(4));
    if (!uris.includes(targetId)) {
      return new Error(`unexpected server SPIFFE ID: ${uris.join(",") || "none"}`);
    }
  },
}, (response) => {
  response.resume();
  console.error(`wrong-client Sidecar unexpectedly returned HTTP ${response.statusCode}`);
  process.exit(1);
});
request.on("timeout", () => {
  console.error("wrong-client test timed out");
  process.exit(1);
});
request.on("error", () => process.exit(0));
NODE

cleanup_wrong_client
remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker start "$BROKER_CONTAINER" >/dev/null
main_ready=0
for _ in $(seq 1 30); do
    current_logs="$(remote_sudo openviking "$OPENVIKING_TARGET" \
        /usr/local/bin/docker logs "$BROKER_CONTAINER" 2>&1)"
    current_ready_count="$(grep -Fc 'OpenViking mTLS listener is ready' <<<"$current_logs")"
    if (( current_ready_count > ready_count_before )); then
        main_ready=1
        break
    fi
    [[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == true ]] || break
    sleep 1
done
[[ "$main_ready" == 1 ]] || fail 'main Broker Sidecar did not recover after the wrong-client test'

target_exit_result='not requested'
if [[ "$VERIFY_TARGET_EXIT" == 1 ]]; then
    remote_sudo openviking "$OPENVIKING_TARGET" \
        /usr/local/bin/docker stop "$OPENVIKING_CONTAINER" >/dev/null
    for _ in $(seq 1 15); do
        [[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == false ]] && break
        sleep 1
    done
    [[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == false ]] \
        || fail 'Broker Sidecar did not stop after the target PID exited'
    target_exit_result='OpenViking stopped; Sidecar exited through pidfd monitoring'
fi

printf '%s\n' \
    'Dual-TDVM ALLOW verification passed.' \
    "OpenClaw Agent parent: $OPENCLAW_PARENT_ID" \
    "OpenViking Agent parent: $OPENVIKING_PARENT_ID" \
    "OpenViking container: $target_container_id (PID $target_pid)" \
    "Broker socket: $broker_socket_stat" \
    "Trustee metric: $allowed_metric" \
    'OpenViking has no SPIRE/SVID mount; only the Broker Sidecar holds workload identity.' \
    'No-client and wrong-expected-client mTLS checks failed as expected.' \
    'OpenClaw Guard ALLOW preceded successful mTLS /health and /ready requests.' \
    "Target exit check: $target_exit_result" \
    'Evidence Provider and Trustee are still mock-stage; this is not real Quote/QGS or Rekor acceptance.'
