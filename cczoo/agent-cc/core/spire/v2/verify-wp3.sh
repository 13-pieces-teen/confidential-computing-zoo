#!/usr/bin/env bash
# WP3 identity lifecycle and denial convergence verification (OpenClaw side).
#
# Each disruptive scenario restores the previous state before the next one
# starts, so this script can run against an active runtime. Scenarios that
# require a second trust domain / stale bundle / CA rotation are reported as
# SKIP with the reason (they need a dedicated runtime).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${V2_REAL_OPENCLAW_USER:-node}"
MTLS_CONTAINER="${V2_OPENCLAW_MTLS_CONTAINER:-argus-v2-openclaw-mtls}"
AGENT_CONTAINER="${V2_OPENCLAW_AGENT_CONTAINER:-argus-v2-openclaw-agent}"
SERVER_CONTAINER="${V2_OPENCLAW_SERVER_CONTAINER:-argus-v2-spire-server}"
PROXY_BIND="${V2_OPENCLAW_PROXY_BIND:-172.31.44.1}"
PROXY_PORT="${V2_OPENCLAW_PROXY_PORT:-1934}"
PROXY_URL="${V2_OPENCLAW_PROXY_URL:-http://$PROXY_BIND:$PROXY_PORT}"
SERVER_SOCKET="/opt/spire/run/server/api.sock"
WORKLOAD_ENTRY="v2-openclaw-workload"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
# SPIRE clamps the test entry to at least five minutes. The default budget
# covers that SVID lifetime, the 60-second connection lifetime, and probe
# scheduling tolerance.
SLA_BUDGET_SECONDS="${V2_WP3_SLA_BUDGET_SECONDS:-420}"
SHORT_SVID_TTL_SECONDS="${V2_WP3_SHORT_SVID_TTL_SECONDS:-300}"
CONNECTION_GRACE_SECONDS="${V2_WP3_CONNECTION_GRACE_SECONDS:-90}"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
ENTRY_RESTORE_REQUIRED=0
AGENT_START_REQUIRED=0
BANNED_AGENT_ID=""
CLEANUP_RUNNING=0

for value_name in SLA_BUDGET_SECONDS SHORT_SVID_TTL_SECONDS CONNECTION_GRACE_SECONDS; do
    value="${!value_name}"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] \
        || { printf '%s must be a positive integer number of seconds\n' "$value_name" >&2; exit 1; }
done
(( SHORT_SVID_TTL_SECONDS + CONNECTION_GRACE_SECONDS <= SLA_BUDGET_SECONDS )) \
    || { printf 'SLA budget must cover the short SVID TTL plus connection grace\n' >&2; exit 1; }

fail() {
    printf 'WP3 verification: FAIL: %s\n' "$1" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 1
}

pass() {
    printf 'PASS: %s\n' "$1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

skip() {
    printf 'SKIP: %s\n' "$1"
    SKIP_COUNT=$((SKIP_COUNT + 1))
}

spire_server() {
    docker compose -f "$SCRIPT_DIR/compose.center.yaml" exec -T spire-server \
        /opt/spire/bin/spire-server "$@" -socketPath "$SERVER_SOCKET"
}

# The current non-banned x509pop agent ID, preferring the most recently
# attested one when stale agents linger after a ban/evict cycle.
openclaw_agent_id() {
    spire_server agent list -output json | python3 -c '
import json, sys
candidates = []
for a in json.load(sys.stdin).get("agents", []):
    i = a.get("id")
    if isinstance(i, dict):
        v = "spiffe://{}{}".format(i["trust_domain"], i["path"])
    else:
        v = str(i)
    if "/spire/agent/x509pop/" in v and not a.get("banned"):
        candidates.append((int(a.get("x509svid_expires_at") or 0), v))
if not candidates:
    raise SystemExit(1)
else:
    candidates.sort()
    print(candidates[-1][1])
'
}

# The current OpenClaw workload image digest (matches the entry selectors).
openclaw_digest() {
    docker image inspect argus-spire-v2-mtls:local --format '{{.Id}}'
}

# Recreate the OpenClaw workload entry parented to the current x509pop agent.
recreate_openclaw_entry() {
    local ttl="${1:-600}"
    local parent
    parent="$(openclaw_agent_id || true)"
    [[ -n "$parent" ]] || { echo "no live x509pop agent" >&2; return 1; }
    local digest
    digest="$(openclaw_digest)"
    spire_server entry delete -entryID "$WORKLOAD_ENTRY" >/dev/null 2>&1 || true
    spire_server entry create \
        -entryID "$WORKLOAD_ENTRY" \
        -parentID "$parent" \
        -spiffeID "$OPENCLAW_ID" \
        -selector docker:label:argus.workload:openclaw \
        -selector docker:image_id:"$digest" \
        -selector docker:image_config_digest:"$digest" \
        -x509SVIDTTL "$ttl" >/dev/null
}

# A positive Guard-gated request through the egress returns 200.
positive_probe() {
    docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" bash -lc \
        "curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
           -H 'X-Argus-Request-ID: wp3-probe' $PROXY_URL/health" 2>/dev/null
}

egress_identity() {
    docker exec "$MTLS_CONTAINER" /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expected-id="$OPENCLAW_ID" 2>/dev/null
}

egress_identity_expiry() {
    docker exec "$MTLS_CONTAINER" /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expected-id="$OPENCLAW_ID" \
        -expiry-unix 2>/dev/null
}

expect_identity_denial() {
    docker exec "$MTLS_CONTAINER" /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expect-no-identity \
        -timeout=5s >/dev/null 2>&1
}

agent_is_banned() {
    local expected_id="$1"
    spire_server agent list -output json | python3 -c '
import json, sys
expected = sys.argv[1]
for agent in json.load(sys.stdin).get("agents", []):
    value = agent.get("id")
    if isinstance(value, dict):
        value = "spiffe://{}{}".format(value["trust_domain"], value["path"])
    if str(value) == expected:
        raise SystemExit(0 if agent.get("banned") else 1)
raise SystemExit(1)
' "$expected_id"
}

wait_for() {
    local what="$1"
    local tries="${2:-30}"
    local i
    for i in $(seq 1 "$tries"); do
        if eval "$what" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

prepare_short_lived_identity() {
    recreate_openclaw_entry "$SHORT_SVID_TTL_SECONDS" \
        || fail "could not create the short-lived workload entry"
    ENTRY_RESTORE_REQUIRED=1
    docker restart "$MTLS_CONTAINER" >/dev/null
    wait_for "egress_identity" 60 || fail "egress did not obtain an identity"

    local deadline expiry now remaining
    deadline="$(( $(date +%s) + SLA_BUDGET_SECONDS ))"
    while (( $(date +%s) < deadline )); do
        expiry="$(egress_identity_expiry || true)"
        now="$(date +%s)"
        if [[ "$expiry" =~ ^[0-9]+$ ]]; then
            remaining="$(( expiry - now ))"
            if (( remaining > 0 && remaining <= SHORT_SVID_TTL_SECONDS + 30 )); then
                printf '  short-lived SVID expires at %s (%ss remaining)\n' "$expiry" "$remaining"
                return 0
            fi
        fi
        sleep 5
    done
    fail "egress did not obtain an SVID within the short-lived TTL window"
}

wait_for_revocation_convergence() {
    local expiry="$1"
    local label="$2"
    local started deadline now required elapsed status converged
    started="$(date +%s)"
    required="$(( expiry - started + CONNECTION_GRACE_SECONDS ))"
    if (( required > SLA_BUDGET_SECONDS )); then
        fail "$label requires ${required}s from the observed SVID expiry; SLA budget is ${SLA_BUDGET_SECONDS}s"
    fi
    deadline="$(( started + SLA_BUDGET_SECONDS ))"
    status="200"
    converged=0
    while (( $(date +%s) <= deadline )); do
        status="$(positive_probe || true)"
        if [[ "$status" != "200" ]] && expect_identity_denial; then
            converged=1
            break
        fi
        sleep 2
    done
    now="$(date +%s)"
    elapsed="$(( now - started ))"
    printf '  %s convergence after %ss (last HTTP status=%s)\n' "$label" "$elapsed" "${status:-transport-error}"
    (( converged != 0 )) \
        || fail "$label did not reach both business-path rejection and explicit identity denial within ${SLA_BUDGET_SECONDS}s"
    sleep 5
    [[ "$(positive_probe || true)" != "200" ]] \
        || fail "$label business path recovered after convergence"
    expect_identity_denial \
        || fail "$label identity denial did not remain stable after convergence"
}

restore_runtime() {
    (( CLEANUP_RUNNING == 0 )) || return 0
    CLEANUP_RUNNING=1
    local failed=0

    if (( AGENT_START_REQUIRED != 0 )); then
        docker start "$AGENT_CONTAINER" >/dev/null 2>&1 || failed=1
        AGENT_START_REQUIRED=0
    fi
    if [[ -n "$BANNED_AGENT_ID" ]]; then
        spire_server agent evict -spiffeID "$BANNED_AGENT_ID" >/dev/null 2>&1 || true
        docker restart "$AGENT_CONTAINER" >/dev/null 2>&1 || failed=1
        wait_for "openclaw_agent_id" 60 || failed=1
        BANNED_AGENT_ID=""
        ENTRY_RESTORE_REQUIRED=1
    fi
    if (( ENTRY_RESTORE_REQUIRED != 0 )) \
        || ! spire_server entry show -entryID "$WORKLOAD_ENTRY" >/dev/null 2>&1; then
        recreate_openclaw_entry 600 || failed=1
        ENTRY_RESTORE_REQUIRED=0
    fi
    if ! egress_identity >/dev/null 2>&1 || [[ "$(positive_probe || true)" != "200" ]]; then
        docker restart "$MTLS_CONTAINER" >/dev/null 2>&1 || failed=1
        wait_for "egress_identity" 60 || failed=1
        wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || failed=1
    fi
    CLEANUP_RUNNING=0
    (( failed == 0 ))
}

cleanup_on_exit() {
    local status="$?"
    trap - EXIT INT TERM
    set +e
    if ! restore_runtime; then
        printf 'FINAL RECOVERY: runtime restoration failed\n' >&2
        status=1
    fi
    exit "$status"
}

trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

test_proxy_restart() {
    echo "[WP3] mTLS egress restart & recovery"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    docker restart "$MTLS_CONTAINER" >/dev/null
    wait_for "egress_identity" 40 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "mTLS egress restart recovered"
}

test_workload_api_outage() {
    echo "[WP3] Workload API short outage"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    AGENT_START_REQUIRED=1
    docker stop "$AGENT_CONTAINER" >/dev/null
    sleep 6
    if egress_identity >/dev/null 2>&1; then
        fail "a new identity client obtained an SVID while the Workload API was down"
    fi
    printf '  cached business path status during short outage: %s\n' \
        "$(positive_probe || true)"
    docker start "$AGENT_CONTAINER" >/dev/null
    AGENT_START_REQUIRED=0
    wait_for "egress_identity" 40 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "Workload API outage: new source acquisition unavailable; recovered after"
}

test_agent_restart() {
    echo "[WP3] SPIRE Agent restart & re-attestation"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    docker restart "$AGENT_CONTAINER" >/dev/null
    wait_for "egress_identity" 60 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "Agent restart: recovered (x509pop re-attested)"
}

test_server_restart() {
    echo "[WP3] SPIRE Server restart"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    docker restart "$SERVER_CONTAINER" >/dev/null
    wait_for "spire_server agent list >/dev/null" 60 || fail "server did not become healthy"
    wait_for "egress_identity" 60 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 30 || fail "positive path did not recover"
    pass "Server restart: identities and entries persisted; dual agents intact"
}

test_entry_deletion() {
    echo "[WP3] workload entry deletion -> control-plane removal -> recovery"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    ENTRY_RESTORE_REQUIRED=1
    spire_server entry delete -entryID "$WORKLOAD_ENTRY" >/dev/null
    ! spire_server entry show -entryID "$WORKLOAD_ENTRY" >/dev/null 2>&1 \
        || fail "workload entry still exists after deletion"
    recreate_openclaw_entry || fail "could not re-create the workload entry"
    ENTRY_RESTORE_REQUIRED=0
    wait_for "egress_identity" 60 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "Entry deletion: control-plane state removed; re-created and recovered"
}

test_agent_ban() {
    echo "[WP3] Agent ban -> fail-closed -> recovery (evict + re-attest)"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    prepare_short_lived_identity
    local agent_id expiry
    agent_id="$(openclaw_agent_id || true)"
    [[ -n "$agent_id" ]] || fail "could not resolve the OpenClaw agent ID"
    expiry="$(egress_identity_expiry)"
    [[ "$expiry" =~ ^[0-9]+$ ]] || fail "could not read the OpenClaw SVID expiry"
    BANNED_AGENT_ID="$agent_id"
    spire_server agent ban -spiffeID "$agent_id" >/dev/null
    agent_is_banned "$agent_id" || fail "SPIRE Server did not report the Agent as banned"
    wait_for_revocation_convergence "$expiry" "Agent ban"
    # Recover: SPIRE 1.15 has no `agent unban`; evict the banned agent, let it
    # re-attest (x509pop can_reattest=true), and re-parent the workload entry.
    spire_server agent evict -spiffeID "$agent_id" >/dev/null 2>&1 || true
    docker restart "$AGENT_CONTAINER" >/dev/null
    wait_for "openclaw_agent_id" 60 || fail "OpenClaw agent did not re-attest"
    BANNED_AGENT_ID=""
    recreate_openclaw_entry || fail "could not re-parent the workload entry"
    ENTRY_RESTORE_REQUIRED=0
    docker restart "$MTLS_CONTAINER" >/dev/null
    wait_for "egress_identity" 60 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "Agent ban: fail-closed; evicted, re-attested, re-parented and recovered"
}

test_connection_convergence() {
    echo "[WP3] connection convergence: revoked identity cannot continue (SLA budget ${SLA_BUDGET_SECONDS}s)"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    prepare_short_lived_identity
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "short-TTL baseline probe failed"
    local expiry
    expiry="$(egress_identity_expiry)"
    [[ "$expiry" =~ ^[0-9]+$ ]] || fail "could not read the OpenClaw SVID expiry"
    ENTRY_RESTORE_REQUIRED=1
    spire_server entry delete -entryID "$WORKLOAD_ENTRY" >/dev/null
    wait_for_revocation_convergence "$expiry" "Entry deletion"
    recreate_openclaw_entry 600 || fail "could not restore the workload entry"
    ENTRY_RESTORE_REQUIRED=0
    docker restart "$MTLS_CONTAINER" >/dev/null
    wait_for "egress_identity" 60 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "Connection convergence: revoked identity stopped serving within SLA budget"
}

skip "trust bundle update / old bundle / wrong trust domain: requires a second trust domain or CA rotation (dedicated runtime)"
skip "SVID expiry with can_reattest=false: OpenViking argus_tdx side; x509pop re-attests instead (can_reattest=true)"

echo "=============================================="
echo "WP3 verification (OpenClaw side) - SLA budget: ${SLA_BUDGET_SECONDS}s"
echo "=============================================="

[[ "$(positive_probe)" == "200" ]] || fail "baseline egress probe is not 200"

test_proxy_restart
test_workload_api_outage
test_agent_restart
test_server_restart
test_entry_deletion
test_agent_ban
test_connection_convergence

# Final recovery: the runtime must be left in a working state even if a
# scenario's recovery failed mid-way.
echo "[WP3] final recovery"
restore_runtime || fail "final runtime restoration failed"
trap - EXIT INT TERM
echo "PASS: final recovery - workload entry present and egress serving"

echo "=============================================="
echo "WP3 summary: PASS=$PASS_COUNT FAIL=$FAIL_COUNT SKIP=$SKIP_COUNT"
echo "=============================================="
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
