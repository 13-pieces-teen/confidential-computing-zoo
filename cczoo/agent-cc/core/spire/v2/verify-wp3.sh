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
# Convergence after identity revocation is bounded by the workload SVID TTL
# (SPIRE clamps entry TTL to its 5-minute minimum), so the budget must cover
# one full TTL plus renewal retries.
SLA_BUDGET_SECONDS="${V2_WP3_SLA_BUDGET_SECONDS:-360}"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

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
    print("")
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
    parent="$(openclaw_agent_id)"
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
    docker stop "$AGENT_CONTAINER" >/dev/null
    sleep 6
    if egress_identity >/dev/null 2>&1; then
        fail "egress obtained identity while the Workload API was down"
    fi
    docker start "$AGENT_CONTAINER" >/dev/null
    wait_for "egress_identity" 40 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "Workload API outage: fail-closed during outage, recovered after"
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
    echo "[WP3] workload entry deletion -> fail-closed -> recovery"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    spire_server entry delete -entryID "$WORKLOAD_ENTRY" >/dev/null
    sleep 3
    if docker exec "$MTLS_CONTAINER" /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expect-no-identity -timeout=5s >/dev/null 2>&1; then
        fail "egress unexpectedly obtained identity after entry deletion"
    fi
    recreate_openclaw_entry || fail "could not re-create the workload entry"
    wait_for "egress_identity" 60 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "Entry deletion: fail-closed; re-created and recovered"
}

test_agent_ban() {
    echo "[WP3] Agent ban -> fail-closed -> recovery (evict + re-attest)"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    local agent_id
    agent_id="$(openclaw_agent_id)"
    [[ -n "$agent_id" ]] || fail "could not resolve the OpenClaw agent ID"
    spire_server agent ban -spiffeID "$agent_id" >/dev/null
    sleep 3
    if docker exec "$MTLS_CONTAINER" /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expect-no-identity -timeout=5s >/dev/null 2>&1; then
        fail "egress unexpectedly obtained identity after agent ban"
    fi
    # Recover: SPIRE 1.15 has no `agent unban`; evict the banned agent, let it
    # re-attest (x509pop can_reattest=true), and re-parent the workload entry.
    spire_server agent evict -spiffeID "$agent_id" >/dev/null 2>&1 || true
    docker restart "$AGENT_CONTAINER" >/dev/null
    wait_for "openclaw_agent_id" 60 || fail "OpenClaw agent did not re-attest"
    recreate_openclaw_entry || fail "could not re-parent the workload entry"
    docker restart "$MTLS_CONTAINER" >/dev/null
    wait_for "egress_identity" 60 || fail "egress identity did not recover"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "positive path did not recover"
    pass "Agent ban: fail-closed; evicted, re-attested, re-parented and recovered"
}

test_connection_convergence() {
    echo "[WP3] connection convergence: revoked identity cannot continue (SLA budget ${SLA_BUDGET_SECONDS}s)"
    [[ "$(positive_probe)" == "200" ]] || fail "baseline probe failed"
    # Issued SVIDs stay valid until expiry, so convergence is bounded by the
    # SVID TTL plus the connection max lifetime. Use a short-TTL entry so the
    # convergence window is observable.
    recreate_openclaw_entry 60 || fail "could not create a short-TTL entry"
    docker restart "$MTLS_CONTAINER" >/dev/null
    wait_for "egress_identity" 60 || fail "egress did not obtain the short-TTL identity"
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || fail "short-TTL baseline probe failed"
    local started elapsed
    started="$(date +%s)"
    spire_server entry delete -entryID "$WORKLOAD_ENTRY" >/dev/null
    # The egress holds a 60s SVID. After it expires it must not renew (entry
    # gone), so the positive path must fail within the SLA budget.
    local i
    for i in $(seq 1 "$((SLA_BUDGET_SECONDS + 10))"); do
        if [[ "$(positive_probe)" != "200" ]]; then
            break
        fi
        sleep 2
    done
    elapsed="$(( $(date +%s) - started ))"
    echo "  convergence (positive path unavailable) after ${elapsed}s"
    if [[ "$(positive_probe)" == "200" ]]; then
        fail "positive path survived beyond the SLA budget (${SLA_BUDGET_SECONDS}s)"
    fi
    [[ "$elapsed" -le "$SLA_BUDGET_SECONDS" ]] \
        || fail "convergence took ${elapsed}s, exceeding the ${SLA_BUDGET_SECONDS}s SLA budget"
    recreate_openclaw_entry 600 || fail "could not restore the workload entry"
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
if ! spire_server entry show -entryID "$WORKLOAD_ENTRY" >/dev/null 2>&1; then
    echo "  restoring the OpenClaw workload entry..."
    recreate_openclaw_entry 600 || { echo "FINAL RECOVERY: could not restore the entry" >&2; exit 1; }
fi
if ! egress_identity >/dev/null 2>&1 || [[ "$(positive_probe)" != "200" ]]; then
    docker restart "$MTLS_CONTAINER" >/dev/null
    wait_for "egress_identity" 60 || { echo "FINAL RECOVERY: egress identity failed" >&2; exit 1; }
    wait_for "[[ \"\$(positive_probe)\" == 200 ]]" 20 || { echo "FINAL RECOVERY: positive path failed" >&2; exit 1; }
fi
echo "PASS: final recovery - workload entry present and egress serving"

echo "=============================================="
echo "WP3 summary: PASS=$PASS_COUNT FAIL=$FAIL_COUNT SKIP=$SKIP_COUNT"
echo "=============================================="
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1
