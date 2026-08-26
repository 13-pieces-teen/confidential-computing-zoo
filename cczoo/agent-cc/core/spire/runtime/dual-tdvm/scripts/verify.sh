#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${DUAL_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
export DUAL_RUNTIME_DIR="$RUNTIME_DIR"
REMOTE_ROOT="${DUAL_GUEST_ROOT:-/opt/argus-spire-dual}"
SERVER_SOCKET="/opt/spire/run/server/api.sock"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
OPENCLAW_BROKER_ID="spiffe://argus.local/infra/openclaw-broker"
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"
BROKER_ID="spiffe://argus.local/infra/openviking-broker"
OPENCLAW_CONTAINER="${DUAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_IMAGE="${DUAL_OPENCLAW_WORKLOAD_IMAGE:-argus-dual-openclaw:local}"
OPENCLAW_BROKER_CONTAINER="${DUAL_OPENCLAW_BROKER_CONTAINER:-argus-dual-openclaw-egress}"
DENY_BROKER_CONTAINER="argus-dual-openclaw-deny-egress"
OPENCLAW_BROKER_IMAGE="${DUAL_OPENCLAW_BROKER_IMAGE:-argus-openclaw-egress-sidecar:local}"
OPENVIKING_CONTAINER="${DUAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
BROKER_CONTAINER="${DUAL_OPENVIKING_BROKER_CONTAINER:-agentcc-openviking-broker-sidecar}"
BROKER_IMAGE="${DUAL_OPENVIKING_BROKER_IMAGE:-argus-openviking-broker-sidecar:local}"
OPENVIKING_SOURCE_IMAGE="${DUAL_OPENVIKING_SOURCE_IMAGE:-localhost:5000/openviking:v0.4.8}"
OPENVIKING_RUNTIME_IMAGE_ID="${DUAL_OPENVIKING_RUNTIME_IMAGE_ID:-openviking-cmem:latest}"
WRONG_CLIENT_CONTAINER="argus-dual-openviking-wrong-client-sidecar"
OPENCLAW_PARENT_ID="${DUAL_OPENCLAW_PARENT_ID:-}"
OPENVIKING_PARENT_ID="${DUAL_OPENVIKING_PARENT_ID:-}"
OPENCLAW_TARGET="${DUAL_OPENCLAW_TDVM_SSH_TARGET:-}"
OPENVIKING_TARGET="${DUAL_OPENVIKING_TDVM_SSH_TARGET:-}"
OPENCLAW_RUN="${DUAL_OPENCLAW_GUEST_RUN:-/run/argus-spire-dual/openclaw}"
OPENCLAW_BROKER_RUN="${DUAL_OPENCLAW_GUEST_BROKER_RUN:-/run/argus-spire-dual/openclaw-broker}"
OPENVIKING_RUN="${DUAL_OPENVIKING_GUEST_RUN:-/run/argus-spire-dual/openviking}"
OPENVIKING_BROKER_RUN="${DUAL_OPENVIKING_GUEST_BROKER_RUN:-/run/argus-spire-dual/openviking-broker}"
OPENVIKING_ORIGIN="${DUAL_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
OPENVIKING_HOST_ADDRESS="${DUAL_OPENVIKING_HOST_ADDRESS:-}"
OPENVIKING_HOST="${OPENVIKING_ORIGIN#https://}"
OPENVIKING_HOST="${OPENVIKING_HOST%%:*}"
OPENVIKING_PORT="${DUAL_OPENVIKING_PORT:-1943}"
OPENCLAW_EGRESS_PORT="${DUAL_OPENCLAW_EGRESS_PORT:-1934}"
OPENCLAW_NETWORK="${DUAL_OPENCLAW_NETWORK:-argus-dual-openclaw}"
TRUSTEE_PORT="${DUAL_TDVM_TRUSTEE_PORT:-18443}"
EXPECTED_DECISION="${DUAL_EXPECT_WORKLOAD_DECISION:-allow}"
VERIFY_TARGET_EXIT="${DUAL_VERIFY_TARGET_EXIT:-1}"
EXPECT_APPLICATION_READY="${DUAL_EXPECT_APPLICATION_READY:-${DUAL_OPENVIKING_APPLICATION_READY:-0}}"

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
[[ "$EXPECT_APPLICATION_READY" == 0 || "$EXPECT_APPLICATION_READY" == 1 ]] \
    || fail 'DUAL_EXPECT_APPLICATION_READY must be 0 or 1'
[[ "$VERIFY_TARGET_EXIT" == 0 || "$VERIFY_TARGET_EXIT" == 1 ]] \
    || fail 'DUAL_VERIFY_TARGET_EXIT must be 0 or 1'
if [[ "$EXPECTED_DECISION" == allow ]]; then
    [[ -n "$OPENVIKING_HOST_ADDRESS" ]] \
        || fail 'DUAL_OPENVIKING_HOST_ADDRESS is required for ALLOW verification'
    [[ -n "${DUAL_OPENVIKING_API_KEY:-}" ]] \
        || fail 'DUAL_OPENVIKING_API_KEY is required for the real plugin E2E'
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

# Prove that both SSH endpoints are TDVMs and expose only their role-local
# Workload and Broker API sockets before inspecting central SPIRE state.
for role in openclaw openviking; do
    if [[ "$role" == openclaw ]]; then
        target="$OPENCLAW_TARGET"
        run_dir="$OPENCLAW_RUN"
        broker_run_dir="$OPENCLAW_BROKER_RUN"
        provider_container=argus-dual-openclaw-evidence
        agent_container=argus-dual-openclaw-agent
    else
        target="$OPENVIKING_TARGET"
        run_dir="$OPENVIKING_RUN"
        broker_run_dir="$OPENVIKING_BROKER_RUN"
        provider_container=argus-dual-openviking-evidence
        agent_container=argus-dual-openviking-agent
    fi
    remote_sudo "$role" "$target" test -c /dev/tdx_guest \
        || fail "$role SSH target is not a TD Guest"
    remote_sudo "$role" "$target" test -S "$run_dir/agent.sock" \
        || fail "$role Workload API socket is missing"
    remote_sudo "$role" "$target" test -S "$broker_run_dir/broker.sock" \
        || fail "$role Broker API socket is missing"
    [[ "$(container_running "$role" "$target" "$provider_container")" == true ]] \
        || fail "$role mock Evidence Provider is not running"
    [[ "$(container_running "$role" "$target" "$agent_container")" == true ]] \
        || fail "$role SPIRE Agent is not running"
done

# Parent IDs must refer to live Agents, not stale IDs from an earlier TDVM run.
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

[[ "$(container_running openviking "$OPENVIKING_TARGET" "$OPENVIKING_CONTAINER")" == true ]] \
    || fail 'TC-API OpenViking container is not running'

# Compare the immutable image actually running in each TDVM with the selectors
# that authorize SVID issuance.
openclaw_image_config_digest="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker image inspect "$OPENCLAW_IMAGE" --format '{{.Id}}')"
openclaw_broker_image_config_digest="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker image inspect "$OPENCLAW_BROKER_IMAGE" --format '{{.Id}}')"
target_pid="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.State.Pid}}')"
target_container_id="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.Id}}')"
source_image_config_digest="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker image inspect "$OPENVIKING_SOURCE_IMAGE" --format '{{.Id}}')"
runtime_image_config_digest="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker image inspect "$OPENVIKING_RUNTIME_IMAGE_ID" --format '{{.Id}}')"
container_image_config_digest="$(remote_sudo openviking "$OPENVIKING_TARGET" \
    /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.Image}}')"
[[ "$source_image_config_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "source image config digest is invalid: $source_image_config_digest"
[[ "$openclaw_image_config_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "OpenClaw image config digest is invalid: $openclaw_image_config_digest"
[[ "$openclaw_broker_image_config_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "OpenClaw Broker image config digest is invalid: $openclaw_broker_image_config_digest"
[[ "$runtime_image_config_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "runtime image config digest is invalid: $runtime_image_config_digest"
[[ "$container_image_config_digest" == "$runtime_image_config_digest" ]] \
    || fail "OpenViking container image digest is $container_image_config_digest, expected runtime digest $runtime_image_config_digest"

# Require one exact entry per identity, under the correct TDVM Agent parent.
# The OpenViking target additionally needs verified workload-attestation claims.
entries="$(spire_server entry show -output json)"
printf '%s' "$entries" | python3 -c '
import json
import sys

expected_parents = {
    "spiffe://argus.local/agent/openclaw": sys.argv[1],
    "spiffe://argus.local/infra/openclaw-broker": sys.argv[1],
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
openclaw_broker = selectors["spiffe://argus.local/infra/openclaw-broker"]
target = selectors["spiffe://argus.local/service/openviking-cmem"]
broker = selectors["spiffe://argus.local/infra/openviking-broker"]
required_openclaw = {
    "docker:label:argus.workload:openclaw",
    "docker:image_id:{}".format(sys.argv[3]),
    "docker:image_config_digest:{}".format(sys.argv[4]),
}
missing = required_openclaw - openclaw
if missing:
    raise SystemExit("OpenClaw entry is missing selectors: {}".format(sorted(missing)))
if not attributes["spiffe://argus.local/agent/openclaw"].get("disable_x509_svid_prefetch", False):
    raise SystemExit("OpenClaw entry does not disable X.509-SVID prefetch")
required_openclaw_broker = {
    "docker:label:argus.component:openclaw-broker",
    "docker:image_id:{}".format(sys.argv[5]),
    "docker:image_config_digest:{}".format(sys.argv[6]),
}
missing = required_openclaw_broker - openclaw_broker
if missing:
    raise SystemExit("OpenClaw Broker entry is missing selectors: {}".format(sorted(missing)))
required_target = {
    "docker:label:argus.workload:openviking-cmem",
    "docker:image_id:{}".format(sys.argv[7]),
    "docker:image_config_digest:{}".format(sys.argv[8]),
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
for name, values in (("OpenClaw", openclaw), ("OpenClaw Broker", openclaw_broker), ("OpenViking target", target), ("OpenViking Broker", broker)):
    for prefix in ("docker:image_id:", "docker:image_config_digest:sha256:"):
        if not any(value.startswith(prefix) for value in values):
            raise SystemExit("{} entry is missing a {} selector".format(name, prefix))
' "$OPENCLAW_PARENT_ID" "$OPENVIKING_PARENT_ID" \
    "$OPENCLAW_IMAGE" "$openclaw_image_config_digest" \
    "$OPENCLAW_BROKER_IMAGE" "$openclaw_broker_image_config_digest" \
    "$OPENVIKING_RUNTIME_IMAGE_ID" "$runtime_image_config_digest"

# The business container must not hold SPIFFE/SVID material: plaintext is
# loopback-only, while identity sockets belong exclusively to the Broker.
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

# Correlate Broker behavior with the Trustee's explicit decision metric.
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
    # DENY means no target SVID and no listener; the Broker remains alive so the
    # test distinguishes a policy rejection from an unrelated process crash.
    [[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == true ]] \
        || fail 'Broker Sidecar stopped instead of waiting without a target identity'
    ! grep -Fq 'OpenViking mTLS listener is ready' <<<"$broker_logs" \
        || fail 'Broker Sidecar listened on 1943 while Mock Trustee decision was DENY'
    if remote_sudo openviking "$OPENVIKING_TARGET" \
        /bin/bash -c "exec 3<>/dev/tcp/127.0.0.1/$OPENVIKING_PORT" >/dev/null 2>&1; then
        fail "Broker Sidecar listened on $OPENVIKING_PORT while Mock Trustee decision was DENY"
    fi
    denied_metric="$(grep -E '^argus_m4_fake_requests_total\{service="workload_trustee",result="denied"\} [1-9][0-9]*$' <<<"$trustee_metrics" || true)"
    [[ -n "$denied_metric" ]] \
        || fail 'Mock Trustee did not record a denied workload verification request'
    printf '%s\n' \
        'Dual-TDVM DENY verification passed.' \
        "OpenViking container: $target_container_id (PID $target_pid, still running)" \
        "OpenViking source image config digest: $source_image_config_digest" \
        "OpenViking runtime image config digest: $runtime_image_config_digest" \
        "OpenViking running container image config digest: $container_image_config_digest" \
        "Target Entry image config digest: $runtime_image_config_digest (verified exact selector)" \
        "Broker socket: $broker_socket_stat" \
        "Trustee metric: $denied_metric" \
        'Target Entry requires verified/workload_id/policy selectors and the observed runtime image digest.' \
        'OpenViking has no Workload API, Broker API, or SVID/private-key mount.' \
        "Only the Broker Sidecar mounts Workload API ($broker_workload_mount) and Broker API ($broker_api_mount) for target PID $target_pid." \
        'The Sidecar received no target SVID, never listened on 1943, and remains waiting without identity.' \
        'DENY is established by the configured Mock Trustee decision and its metric, not inferred from the empty Broker snapshot.' \
        'Evidence Provider and Trustee are still mock-stage.'
    exit 0
fi

# ALLOW requires the Broker to receive the strongly selected OpenViking SVID.
[[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == true ]] \
    || fail 'Broker Sidecar is not running after Trustee ALLOW'
grep -Fq "OpenViking mTLS listener is ready for identity $OPENVIKING_ID" <<<"$broker_logs" \
    || fail 'Broker Sidecar did not receive the strongly selected target SVID'
allowed_metric="$(grep -E '^argus_m4_fake_requests_total\{service="workload_trustee",result="ok"\} [1-9][0-9]*$' <<<"$trustee_metrics" || true)"
[[ -n "$allowed_metric" ]] \
    || fail 'Mock Trustee did not record an allowed workload verification request'
[[ "$(container_running openclaw "$OPENCLAW_TARGET" "$OPENCLAW_CONTAINER")" == true ]] \
    || fail 'OpenClaw workload is not running'
[[ "$(container_running openclaw "$OPENCLAW_TARGET" "$OPENCLAW_BROKER_CONTAINER")" == true ]] \
    || fail 'OpenClaw Egress Broker is not running'

# OpenClaw follows the same boundary: it holds no SPIFFE/SVID material. Its
# Egress Broker owns that material, its Guard client token, and the remote address.
openclaw_pid="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_CONTAINER" --format '{{.State.Pid}}')"
openclaw_mounts="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_CONTAINER" \
    --format '{{range .Mounts}}{{println .Destination}}{{end}}')"
if grep -Eq '^(/opt/spire|/run/spire|/run/argus-svid|/run/secrets/argus_guard_api_token)(/|$)' <<<"$openclaw_mounts"; then
    fail 'OpenClaw directly mounts SPIRE, SVID, or Guard material'
fi
openclaw_env="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_CONTAINER" \
    --format '{{range .Config.Env}}{{println .}}{{end}}')"
if grep -Eq '^(ARGUS_|SPIFFE_|NODE_OPTIONS=)' <<<"$openclaw_env"; then
    fail 'OpenClaw contains Argus, SPIFFE, or NODE_OPTIONS injection'
fi
openclaw_entrypoint="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_CONTAINER" \
    --format '{{json .Config.Entrypoint}} {{json .Config.Cmd}}')"
if grep -Eqi 'argus|preload|run-openclaw-spiffe' <<<"$openclaw_entrypoint"; then
    fail "OpenClaw uses an Argus entrypoint or preload: $openclaw_entrypoint"
fi
openclaw_extra_hosts="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_CONTAINER" --format '{{json .HostConfig.ExtraHosts}}')"
if grep -Fq 'openviking.argus.local' <<<"$openclaw_extra_hosts"; then
    fail 'OpenClaw directly maps the external OpenViking host'
fi
remote_sudo openclaw "$OPENCLAW_TARGET" /usr/local/bin/docker exec "$OPENCLAW_CONTAINER" \
    sh -c 'test ! -e /usr/local/bin/argus-svid-materializer && test ! -e /opt/argus/openclaw-spiffe/preload.mjs && test ! -e /usr/local/bin/run-openclaw-spiffe.sh' \
    || fail 'OpenClaw runtime image contains Argus materializer or preload code'

openclaw_broker_command="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_BROKER_CONTAINER" --format '{{json .Config.Cmd}}')"
[[ "$openclaw_broker_command" == *"-target-pid=$openclaw_pid"* ]] \
    || fail "OpenClaw Egress Broker does not reference current host PID $openclaw_pid"
[[ "$openclaw_broker_command" == *"-target=$OPENVIKING_ORIGIN"* \
    && "$openclaw_broker_command" == *"-server-spiffe-id=$OPENVIKING_ID"* ]] \
    || fail 'OpenClaw Egress Broker does not use the fixed OpenViking origin and SPIFFE ID'
openclaw_broker_user="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_BROKER_CONTAINER" --format '{{.Config.User}}')"
[[ "$openclaw_broker_user" == '1000:1000' ]] \
    || fail "OpenClaw Egress Broker user is $openclaw_broker_user, expected 1000:1000"
openclaw_broker_workload_mount="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_BROKER_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/opt/spire/run/agent"}}{{.Source}}{{end}}{{end}}')"
openclaw_broker_api_mount="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_BROKER_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/opt/spire/run/broker"}}{{.Source}}{{end}}{{end}}')"
[[ "$openclaw_broker_workload_mount" == "$OPENCLAW_RUN" ]] \
    || fail "OpenClaw Broker Workload API mount is $openclaw_broker_workload_mount, expected $OPENCLAW_RUN"
[[ "$openclaw_broker_api_mount" == "$OPENCLAW_BROKER_RUN" ]] \
    || fail "OpenClaw Broker API mount is $openclaw_broker_api_mount, expected $OPENCLAW_BROKER_RUN"
openclaw_broker_guard_mount="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_BROKER_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/run/secrets/argus_guard_api_token"}}{{.Source}}{{end}}{{end}}')"
[[ "$openclaw_broker_guard_mount" == "$REMOTE_ROOT/secrets/openclaw-guard-api-token" ]] \
    || fail "OpenClaw Egress Guard token mount is $openclaw_broker_guard_mount"
openclaw_broker_extra_hosts="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker inspect "$OPENCLAW_BROKER_CONTAINER" --format '{{json .HostConfig.ExtraHosts}}')"
[[ "$openclaw_broker_extra_hosts" == *"$OPENVIKING_HOST:$OPENVIKING_HOST_ADDRESS"* ]] \
    || fail 'OpenClaw Egress Broker is missing the external OpenViking host mapping'
openclaw_broker_ports="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker port "$OPENCLAW_BROKER_CONTAINER")"
[[ -z "$openclaw_broker_ports" ]] \
    || fail "OpenClaw Egress Broker unexpectedly publishes a TDVM host port: $openclaw_broker_ports"
openclaw_broker_logs="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker logs "$OPENCLAW_BROKER_CONTAINER" 2>&1)"
grep -Fq "OpenClaw Egress Broker is ready for identity $OPENCLAW_ID" <<<"$openclaw_broker_logs" \
    || fail 'OpenClaw Egress Broker did not receive the real OpenClaw PID identity'

# Bypass attempts must fail at both boundaries: mTLS rejects a client without an
# SVID, and the OpenClaw TDVM cannot reach OpenViking's plaintext listener.
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

# Inject a Guard policy miss through a temporary Egress Broker and require the
# denial before any upstream OpenViking request is attempted.
cleanup_guard_deny_broker() {
    remote_sudo openclaw "$OPENCLAW_TARGET" \
        /usr/local/bin/docker rm -f "$DENY_BROKER_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup_guard_deny_broker EXIT
remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker run -d \
    --name "$DENY_BROKER_CONTAINER" \
    --network "$OPENCLAW_NETWORK" \
    --pid host \
    --label argus.component=openclaw-broker \
    --add-host "$OPENVIKING_HOST:$OPENVIKING_HOST_ADDRESS" \
    --volume "$OPENCLAW_RUN:/opt/spire/run/agent:ro" \
    --volume "$OPENCLAW_BROKER_RUN:/opt/spire/run/broker:ro" \
    --volume "$REMOTE_ROOT/secrets/openclaw-guard-api-token:/run/secrets/argus_guard_api_token:ro" \
    "$OPENCLAW_BROKER_IMAGE" \
    -workload-api=unix:///opt/spire/run/agent/agent.sock \
    -broker-socket=/opt/spire/run/broker/broker.sock \
    "-broker-spiffe-id=$OPENCLAW_BROKER_ID" \
    "-agent-spiffe-id=$OPENCLAW_PARENT_ID" \
    "-target-spiffe-id=$OPENCLAW_ID" \
    "-server-spiffe-id=$OPENVIKING_ID" \
    "-target-pid=$openclaw_pid" \
    -listen=0.0.0.0:1935 \
    "-target=$OPENVIKING_ORIGIN" \
    -guard-url=http://argus-dual-openclaw-guard:8007/guard/v1/authorize \
    -guard-token-file=/run/secrets/argus_guard_api_token \
    -target-service=openviking-cmem-deny-test \
    -data-class=sensitive >/dev/null
deny_ready=0
for _ in $(seq 1 30); do
    deny_broker_logs="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
        /usr/local/bin/docker logs "$DENY_BROKER_CONTAINER" 2>&1)"
    if grep -Fq "OpenClaw Egress Broker is ready for identity $OPENCLAW_ID" <<<"$deny_broker_logs"; then
        deny_ready=1
        break
    fi
    [[ "$(container_running openclaw "$OPENCLAW_TARGET" "$DENY_BROKER_CONTAINER")" == true ]] || break
    sleep 1
done
[[ "$deny_ready" == 1 ]] || fail 'temporary Guard-DENY Egress Broker did not become ready'
remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker exec -i "$OPENCLAW_CONTAINER" node - \
    "http://$DENY_BROKER_CONTAINER:1935/health" <<'NODE'
(async () => {
  const response = await fetch(process.argv[2], { signal: AbortSignal.timeout(10000) });
  if (response.status !== 403) {
    throw new Error(`Guard-DENY Egress returned HTTP ${response.status}, expected 403`);
  }
  console.log("Guard DENY returned HTTP 403 before an OpenViking upstream request");
})().catch((error) => { console.error(error); process.exit(1); });
NODE
deny_broker_logs="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker logs "$DENY_BROKER_CONTAINER" 2>&1)"
grep -Eq 'request_id=[0-9a-f]+ method=GET path=/health decision_id=[^ ]+ status=403 duration=' <<<"$deny_broker_logs" \
    || fail 'temporary Egress Broker did not log the Guard DENY result'
cleanup_guard_deny_broker
trap - EXIT

# Exercise the intended cross-TDVM path from inside OpenClaw: local HTTP,
# caller-side Guard, SPIFFE mTLS, then the OpenViking health endpoints.
remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker exec -i "$OPENCLAW_CONTAINER" node - \
    "http://$OPENCLAW_BROKER_CONTAINER:$OPENCLAW_EGRESS_PORT" "$EXPECT_APPLICATION_READY" <<'NODE'
const baseUrl = process.argv[2].replace(/\/+$/, "");
const expectApplicationReady = process.argv[3] === "1";

(async () => {
  const requestStatus = async (path) => (await fetch(`${baseUrl}${path}`, {
    signal: AbortSignal.timeout(10000),
  })).status;
  const healthStatus = await requestStatus("/health");
  if (healthStatus !== 200) throw new Error(`OpenViking /health returned HTTP ${healthStatus}`);
  console.log(`local Egress -> Guard ALLOW -> SPIFFE mTLS /health -> HTTP ${healthStatus}`);

  try {
    const readyStatus = await requestStatus("/ready");
    if (readyStatus === 200) {
      console.log("Application Readiness: READY - /ready HTTP 200");
    } else {
      const message = `Application Readiness: NOT READY - /ready HTTP ${readyStatus}`;
      if (expectApplicationReady) throw new Error(message);
      console.log(message);
    }
  } catch (error) {
    if (expectApplicationReady) throw error;
    console.log(`Application Readiness: NOT READY - /ready request failed: ${error.message}`);
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
NODE

remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker exec -i -u node "$OPENCLAW_CONTAINER" node - \
    /home/node/.openclaw/openclaw.json \
    "http://$OPENCLAW_BROKER_CONTAINER:$OPENCLAW_EGRESS_PORT" <<'NODE'
const { execFileSync } = require("node:child_process");
const configPath = process.argv[2];
const target = process.argv[3].replace(/\/+$/, "");
function get(path) {
  return JSON.parse(execFileSync("openclaw", ["config", "get", path, "--json"], {
    encoding: "utf8",
    env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
  }).trim());
}
const slot = get("plugins.slots.contextEngine");
const plugin = get("plugins.entries.openviking.config");
if (slot !== "openviking") throw new Error(`unexpected context engine ${slot}`);
if (plugin?.mode !== "remote" || plugin?.baseUrl?.replace(/\/+$/, "") !== target) {
  throw new Error(`OpenViking plugin does not target ${target}`);
}
if (typeof plugin.apiKey !== "string" || !plugin.apiKey) throw new Error("plugin API key is missing");
console.log(`OpenViking plugin baseUrl: ${target}`);
NODE
remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker exec -u node \
    -e OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json \
    "$OPENCLAW_CONTAINER" openclaw openviking status --json >/dev/null
openclaw_broker_logs="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
    /usr/local/bin/docker logs "$OPENCLAW_BROKER_CONTAINER" 2>&1)"
grep -Eq 'request_id=[0-9a-f]+ method=GET path=/health decision_id=[^ ]+ status=200 duration=' <<<"$openclaw_broker_logs" \
    || fail 'OpenClaw Egress Broker did not log the allowed /health decision result'

# Finish with a real plugin turn so transport success is not mistaken for
# application-level OpenClaw/OpenViking integration.
remote_sudo openclaw "$OPENCLAW_TARGET" test -x "$REMOTE_ROOT/bin/verify_openclaw_plugin_e2e.sh" \
    || fail 'real OpenClaw plugin E2E script is missing; load the OpenClaw workload again'
plugin_e2e_result="$(remote_sudo openclaw "$OPENCLAW_TARGET" env \
    PATH=/usr/local/bin:/usr/bin:/bin \
    "OPENVIKING_API_KEY=$DUAL_OPENVIKING_API_KEY" \
    "DUAL_OPENCLAW_CONTAINER=$OPENCLAW_CONTAINER" \
    DUAL_OPENCLAW_USER=node \
    DUAL_OPENCLAW_CONFIG=/home/node/.openclaw/openclaw.json \
    "DUAL_OPENVIKING_PLUGIN_BASE_URL=http://$OPENCLAW_BROKER_CONTAINER:$OPENCLAW_EGRESS_PORT" \
    DUAL_GUARD_CONTAINER=argus-dual-openclaw-guard \
    "$REMOTE_ROOT/bin/verify_openclaw_plugin_e2e.sh")" \
    || fail 'real OpenClaw agent turn, OpenViking session capture, or commit E2E failed'
printf '%s\n' "$plugin_e2e_result"

# Replace the Ingress Broker with one expecting the wrong client identity; TLS
# must fail even though both sides otherwise hold valid SPIFFE credentials.
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
    "http://$OPENCLAW_BROKER_CONTAINER:$OPENCLAW_EGRESS_PORT/health" <<'NODE'
(async () => {
  const response = await fetch(process.argv[2], { signal: AbortSignal.timeout(10000) });
  if (response.status !== 502) {
    throw new Error(`wrong-client OpenViking Sidecar produced Egress HTTP ${response.status}, expected 502`);
  }
  console.log("Wrong expected client SPIFFE ID was rejected; Egress returned HTTP 502");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
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
    # PID binding is a lifecycle boundary: each Broker must exit with its target and
    # a restarted workload must be paired with its new host PID.
    remote_sudo openviking "$OPENVIKING_TARGET" \
        /usr/local/bin/docker stop "$OPENVIKING_CONTAINER" >/dev/null
    for _ in $(seq 1 15); do
        [[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == false ]] && break
        sleep 1
    done
    [[ "$(container_running openviking "$OPENVIKING_TARGET" "$BROKER_CONTAINER")" == false ]] \
        || fail 'Broker Sidecar did not stop after the target PID exited'
    if remote_sudo openviking "$OPENVIKING_TARGET" \
        /bin/bash -c "exec 3<>/dev/tcp/127.0.0.1/$OPENVIKING_PORT" >/dev/null 2>&1; then
        fail "port $OPENVIKING_PORT remained reachable after the target PID and Broker Sidecar exited"
    fi
    remote_sudo openclaw "$OPENCLAW_TARGET" \
        /usr/local/bin/docker stop "$OPENCLAW_CONTAINER" >/dev/null
    for _ in $(seq 1 15); do
        [[ "$(container_running openclaw "$OPENCLAW_TARGET" "$OPENCLAW_BROKER_CONTAINER")" == false ]] && break
        sleep 1
    done
    [[ "$(container_running openclaw "$OPENCLAW_TARGET" "$OPENCLAW_BROKER_CONTAINER")" == false ]] \
        || fail 'OpenClaw Egress Broker did not stop after the target PID exited'
    DUAL_RUNTIME_DIR="$RUNTIME_DIR" "$SCRIPT_DIR/manage-guest.sh" openclaw start-workload
    restarted_openclaw_pid="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
        /usr/local/bin/docker inspect "$OPENCLAW_CONTAINER" --format '{{.State.Pid}}')"
    [[ "$restarted_openclaw_pid" != "$openclaw_pid" ]] \
        || fail "OpenClaw restarted with the same host PID $openclaw_pid"
    restarted_broker_command="$(remote_sudo openclaw "$OPENCLAW_TARGET" \
        /usr/local/bin/docker inspect "$OPENCLAW_BROKER_CONTAINER" --format '{{json .Config.Cmd}}')"
    [[ "$restarted_broker_command" == *"-target-pid=$restarted_openclaw_pid"* ]] \
        || fail "restarted Egress Broker does not reference new OpenClaw PID $restarted_openclaw_pid"
    target_exit_result="OpenViking Ingress and OpenClaw Egress Brokers exited with their target PIDs; OpenClaw Egress restarted against new PID $restarted_openclaw_pid"
fi

if [[ "$EXPECT_APPLICATION_READY" == 1 ]]; then
    application_readiness_result='Application /ready = 200 through SPIFFE mTLS (application gate enabled).'
else
    application_readiness_result='Application /ready was recorded separately and was not a security-chain hard gate.'
fi

printf '%s\n' \
    'Dual-TDVM ALLOW verification passed.' \
    "OpenClaw Agent parent: $OPENCLAW_PARENT_ID" \
    "OpenClaw container PID before lifecycle check: $openclaw_pid" \
    "OpenViking Agent parent: $OPENVIKING_PARENT_ID" \
    "OpenViking container: $target_container_id (PID $target_pid)" \
    "OpenViking source image config digest: $source_image_config_digest" \
    "OpenViking runtime image config digest: $runtime_image_config_digest" \
    "OpenViking running container image config digest: $container_image_config_digest" \
    "Target Entry image config digest: $runtime_image_config_digest (verified exact selector)" \
    "Broker socket: $broker_socket_stat" \
    "Trustee metric: $allowed_metric" \
    'Target Entry requires verified/workload_id/policy selectors and matches the observed runtime image digest.' \
    "Broker Sidecar received target SVID $OPENVIKING_ID and opened port $OPENVIKING_PORT." \
    'OpenViking has no Workload API, Broker API, or SVID/private-key mount.' \
    "Only the Broker Sidecar mounts Workload API ($broker_workload_mount) and Broker API ($broker_api_mount) for target PID $target_pid." \
    'No-client and wrong-expected-client mTLS checks failed as expected.' \
    'OpenClaw could not reach the OpenViking plaintext port 1933.' \
    'OpenClaw local HTTP Egress called Guard before successful SPIFFE mTLS /health = 200.' \
    "OpenViking plugin baseUrl: http://$OPENCLAW_BROKER_CONTAINER:$OPENCLAW_EGRESS_PORT" \
    "$application_readiness_result" \
    "Target exit check: $target_exit_result" \
    'Evidence Provider and Trustee are still mock-stage; this is not real Quote/QGS or Rekor acceptance.'
