#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
AGENT_BINARY="${ARGUS_AGENT_BINARY:-/opt/spire-1.15.2/bin/spire-agent}"
AGENT_CONFIG="${ARGUS_AGENT_CONFIG:-/etc/spire/argus-poc/agent.conf}"
AGENT_LOG="${ARGUS_AGENT_LOG:-/var/log/argus-node-attestation/agent.log}"
AGENT_PID_FILE="${ARGUS_AGENT_PID_FILE:-/run/argus-node-attestation/agent.pid}"
POLICY_NOT_AFTER="${ARGUS_POLICY_NOT_AFTER:-}"
EXPECTED_AGENT_ID="${ARGUS_AGENT_ID:-spiffe://argus.local/spire/agent/argus_tdx/openviking-node}"
SERVER_BINARY="${ARGUS_SERVER_BINARY:-/opt/spire-1.15.2/bin/spire-server}"
SERVER_SOCKET="${ARGUS_SERVER_SOCKET:-/run/spire/server/private/api.sock}"
TRANSPORT_TIMEOUT="${ARGUS_TRANSPORT_TIMEOUT:-8}"

fail() {
    printf 'NODE_ATTESTATION=FAIL\nERROR=%s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

config_string() {
    local key="$1"
    sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" "$AGENT_CONFIG" \
        | head -n 1
}

config_port() {
    local value
    value="$(config_string server_port)"
    if [[ -z "$value" ]]; then
        value="$(
            sed -n 's/^[[:space:]]*server_port[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
                "$AGENT_CONFIG" | head -n 1
        )"
    fi
    printf '%s\n' "$value"
}

agent_pid() {
    local pid command_line
    if [[ -s "$AGENT_PID_FILE" ]]; then
        pid="$(cat "$AGENT_PID_FILE")"
        if [[ "$pid" =~ ^[0-9]+$ ]] \
            && command_line="$(ps -p "$pid" -o args= 2>/dev/null)" \
            && [[ "$command_line" == *"$AGENT_BINARY run"* ]] \
            && [[ "$command_line" == *"$AGENT_CONFIG"* ]]; then
            printf '%s\n' "$pid"
            return
        fi
    fi

    ps -eo pid=,args= | awk -v binary="$AGENT_BINARY" -v config="$AGENT_CONFIG" '
        $2 == binary && $3 == "run" && index($0, config) {
            print $1
            found = 1
            exit
        }
        END { exit(found ? 0 : 1) }
    '
}

check_policy_window() {
    local now expiry
    [[ -n "$POLICY_NOT_AFTER" ]] || fail 'ARGUS_POLICY_NOT_AFTER is required'
    expiry="$(date -u -d "$POLICY_NOT_AFTER" +%s 2>/dev/null)" \
        || fail "invalid ARGUS_POLICY_NOT_AFTER: $POLICY_NOT_AFTER"
    now="$(date -u +%s)"
    (( now < expiry )) || fail "POC policy expired at $POLICY_NOT_AFTER"
    printf 'POC_POLICY_VALID=YES\n'
    printf 'POC_POLICY_NOT_AFTER=%s\n' "$POLICY_NOT_AFTER"
    printf 'POC_POLICY_SECONDS_REMAINING=%s\n' "$((expiry - now))"
}

check_config() {
    local plugin proof_key
    [[ -r "$AGENT_CONFIG" ]] || fail "Agent config is not readable: $AGENT_CONFIG"
    plugin="$(
        sed -n 's/^[[:space:]]*NodeAttestor[[:space:]]*"\([^"]*\)".*/\1/p' "$AGENT_CONFIG" \
            | head -n 1
    )"
    [[ "$plugin" == argus_tdx ]] || fail "NodeAttestor must be argus_tdx, got: ${plugin:-NONE}"

    if grep -Eiq 'insecure_bootstrap[[:space:]]*=[[:space:]]*true|join_token' "$AGENT_CONFIG"; then
        fail 'insecure bootstrap or join token configuration is forbidden'
    fi

    proof_key="$(config_string proof_key_path)"
    [[ -n "$proof_key" && -f "$proof_key" ]] || fail 'proof key path is missing or not a file'
    [[ "$(stat -c '%a' "$proof_key")" == 600 ]] || fail 'proof key permissions must be 0600'

    printf 'AGENT_CONFIG=%s\n' "$AGENT_CONFIG"
    printf 'NODE_ATTESTOR=argus_tdx\n'
    printf 'PROOF_KEY_PRESENT=YES\n'
    printf 'PROOF_KEY_CONTENT_EXPOSED=NO\n'
}

check_bundle() {
    local bundle begins ends
    bundle="$(config_string trust_bundle_path)"
    [[ -n "$bundle" && -r "$bundle" ]] || fail 'trust bundle is missing or unreadable'
    grep -Eq '^-----BEGIN CERTIFICATE-----$' "$bundle" \
        || fail 'trust bundle contains no certificate'
    if grep -Eqi 'PRIVATE KEY|BEGIN (RSA |EC |OPENSSH )?PRIVATE' "$bundle"; then
        fail 'trust bundle contains private material'
    fi
    begins="$(grep -c '^-----BEGIN CERTIFICATE-----$' "$bundle")"
    ends="$(grep -c '^-----END CERTIFICATE-----$' "$bundle")"
    [[ "$begins" == "$ends" ]] || fail 'trust bundle has unbalanced PEM blocks'
    openssl crl2pkcs7 -nocrl -certfile "$bundle" \
        | openssl pkcs7 -print_certs -noout >/dev/null \
        || fail 'trust bundle is not valid PEM'

    printf 'TRUST_BUNDLE=%s\n' "$bundle"
    printf 'TRUST_BUNDLE_SHA256=%s\n' "$(sha256sum "$bundle" | awk '{print $1}')"
    printf 'TRUST_BUNDLE_CERTIFICATES=%s\n' "$begins"
    printf 'PRIVATE_MATERIAL_PRESENT=NO\n'
}

check_provider() {
    local socket
    socket="$(config_string evidence_socket_path)"
    [[ -n "$socket" && -S "$socket" ]] || fail "Evidence Provider socket is unavailable: ${socket:-NONE}"
    printf 'EVIDENCE_PROVIDER_SOCKET=%s\n' "$socket"
    printf 'EVIDENCE_PROVIDER_READY=YES\n'
}

check_transport() {
    local address port bundle output error_file response_file header rc
    require_command nc
    require_command openssl
    require_command xxd

    address="$(config_string server_address)"
    port="$(config_port)"
    bundle="$(config_string trust_bundle_path)"
    [[ -n "$address" && -n "$port" ]] || fail 'server address or port is missing'

    nc -z -w "$TRANSPORT_TIMEOUT" "$address" "$port" \
        || fail "TCP connection failed: $address:$port"
    printf 'SERVER_ENDPOINT=%s:%s\n' "$address" "$port"
    printf 'TCP_TRANSPORT=PASS\n'

    output="$(mktemp)"
    error_file="$(mktemp)"
    response_file="$(mktemp)"

    if ! timeout "$TRANSPORT_TIMEOUT" openssl s_client \
        -connect "$address:$port" \
        -CAfile "$bundle" \
        -verify_return_error \
        -alpn h2 </dev/null >"$output" 2>"$error_file"; then
        grep -q 'Verify return code: 0 (ok)' "$output" \
            || fail "TLS certificate verification failed: $address:$port"
    fi
    grep -q 'Verify return code: 0 (ok)' "$output" \
        || fail "TLS certificate verification failed: $address:$port"
    grep -q 'ALPN protocol: h2' "$output" \
        || fail "Server did not negotiate ALPN h2: $address:$port"
    printf 'TLS_CERTIFICATE_VERIFY=PASS\n'
    printf 'ALPN=h2\n'

    set +e
    {
        printf 'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'
        sleep 2
    } | timeout "$TRANSPORT_TIMEOUT" openssl s_client \
        -quiet \
        -connect "$address:$port" \
        -CAfile "$bundle" \
        -verify_return_error \
        -alpn h2 >"$response_file" 2>"$error_file"
    rc=$?
    set -e
    [[ "$rc" -eq 0 || "$rc" -eq 124 ]] || fail "HTTP/2 probe failed with exit code $rc"
    header="$(xxd -p -l 9 "$response_file")"
    [[ "$header" == 000006040000000000 ]] \
        || fail "HTTP/2 SETTINGS was not received from $address:$port"
    printf 'HTTP2_SETTINGS=PASS\n'
    rm -f "$output" "$error_file" "$response_file"
}

preflight() {
    require_command date
    require_command openssl
    require_command sha256sum
    check_policy_window
    check_config
    check_bundle
    check_provider
    check_transport
    printf 'NODE_ATTESTATION_PREFLIGHT=PASS\n'
}

progress_from_log() {
    local log="$1" agent_id
    [[ -r "$log" ]] || fail "Agent log is not readable: $log"

    grep -q 'plugin_name=argus_tdx.*plugin_type=NodeAttestor' "$log" \
        && printf 'PHASE_PLUGIN=PASS\n' \
        || printf 'PHASE_PLUGIN=NOT_OBSERVED\n'
    grep -q 'Bundle loaded' "$log" \
        && printf 'PHASE_BUNDLE=PASS\n' \
        || printf 'PHASE_BUNDLE=NOT_OBSERVED\n'
    grep -q 'Starting node attestation' "$log" \
        && printf 'PHASE_NODE_ATTESTATION_STARTED=YES\n' \
        || printf 'PHASE_NODE_ATTESTATION_STARTED=NO\n'

    if grep -q 'Node attestation was successful' "$log"; then
        agent_id="$(
            grep 'Node attestation was successful' "$log" \
                | tail -n 1 \
                | sed -n 's/.*spiffe_id="\([^"]*\)".*/\1/p'
        )"
        printf 'PHASE_NODE_ATTESTATION=PASS\n'
        printf 'AGENT_SPIFFE_ID=%s\n' "${agent_id:-UNKNOWN}"
        if [[ "$agent_id" == "$EXPECTED_AGENT_ID" ]]; then
            printf 'AGENT_SPIFFE_ID_MATCH=PASS\n'
        else
            printf 'AGENT_SPIFFE_ID_MATCH=FAIL\n'
        fi
        printf 'AGENT_SVID_STATUS=ISSUED_LOCAL_ATTESTATION_RESULT\n'
    elif grep -Eqi 'level=(error|fatal)|Node attestation.*fail' "$log"; then
        printf 'PHASE_NODE_ATTESTATION=FAIL\n'
        printf 'AGENT_SPIFFE_ID=NONE\n'
        printf 'AGENT_SVID_STATUS=NOT_ISSUED\n'
    else
        printf 'PHASE_NODE_ATTESTATION=IN_PROGRESS_OR_NOT_STARTED\n'
        printf 'AGENT_SPIFFE_ID=NONE\n'
        printf 'AGENT_SVID_STATUS=UNKNOWN\n'
    fi
}

status() {
    local pid socket
    if pid="$(agent_pid)"; then
        printf 'AGENT_RUNNING=YES\n'
        printf 'AGENT_PID=%s\n' "$pid"
    else
        printf 'AGENT_RUNNING=NO\n'
    fi

    socket="$(config_string socket_path)"
    if [[ -n "$socket" && -S "$socket" ]] \
        && "$AGENT_BINARY" healthcheck -socketPath "$socket" >/dev/null 2>&1; then
        printf 'AGENT_HEALTH=PASS\n'
    else
        printf 'AGENT_HEALTH=FAIL\n'
    fi

    if [[ -r "$AGENT_LOG" ]]; then
        printf 'AGENT_LOG=%s\n' "$AGENT_LOG"
        progress_from_log "$AGENT_LOG"
    else
        printf 'AGENT_LOG=%s\n' "$AGENT_LOG"
        printf 'PHASE_NODE_ATTESTATION=NOT_OBSERVED\n'
        printf 'AGENT_SPIFFE_ID=NONE\n'
        printf 'AGENT_SVID_STATUS=UNKNOWN\n'
    fi
}

watch_log() {
    local parser_status
    [[ -r "$AGENT_LOG" ]] || fail "Agent log is not readable: $AGENT_LOG"
    set +e
    tail -n 0 -F "$AGENT_LOG" | awk '
        /Configured plugin/ && /plugin_name=argus_tdx/ {
            print strftime("%Y-%m-%dT%H:%M:%SZ", systime(), 1), "[2/6] argus_tdx plugin configured"
            fflush()
        }
        /Bundle loaded/ {
            print strftime("%Y-%m-%dT%H:%M:%SZ", systime(), 1), "[3/6] trust bundle loaded"
            fflush()
        }
        /Starting node attestation/ {
            print strftime("%Y-%m-%dT%H:%M:%SZ", systime(), 1), "[4/6] node attestation started"
            fflush()
        }
        /Node attestation was successful/ {
            id = $0
            sub(/^.*spiffe_id="/, "", id)
            sub(/".*$/, "", id)
            print strftime("%Y-%m-%dT%H:%M:%SZ", systime(), 1), "[5/6] node attestation succeeded"
            print "AGENT_SPIFFE_ID=" id
            print "AGENT_SVID_STATUS=ISSUED"
            fflush()
            exit
        }
        /level=(error|fatal)/ {
            print strftime("%Y-%m-%dT%H:%M:%SZ", systime(), 1), \
                "ERROR=Agent reported an error; inspect the protected Agent log"
            fflush()
        }
    '
    parser_status="${PIPESTATUS[1]}"
    set -e
    return "$parser_status"
}

run_agent() {
    local pid
    preflight
    if pid="$(agent_pid)"; then
        fail "Agent is already running with PID $pid; use status or watch"
    fi

    mkdir -p "$(dirname "$AGENT_LOG")"
    touch "$AGENT_LOG"
    chmod 0600 "$AGENT_LOG"

    printf '%s [1/6] preflight passed\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'AGENT_LOG=%s\n' "$AGENT_LOG"
    "$AGENT_BINARY" run -config "$AGENT_CONFIG" 2>&1 \
        | tee -a "$AGENT_LOG"
}

server_status() {
    [[ -x "$SERVER_BINARY" ]] || fail "SPIRE Server binary is not executable: $SERVER_BINARY"
    [[ -S "$SERVER_SOCKET" ]] || fail "SPIRE Server API socket is unavailable: $SERVER_SOCKET"
    "$SERVER_BINARY" healthcheck -socketPath "$SERVER_SOCKET"
    "$SERVER_BINARY" agent show \
        -socketPath "$SERVER_SOCKET" \
        -spiffeID "$EXPECTED_AGENT_ID"
}

usage() {
    cat <<'EOF'
Usage: argus-node-attestation.sh ACTION

Actions:
  preflight      Validate policy time, config, bundle, provider, TLS and HTTP/2
  run            Run one Agent process in the foreground and save its log
  status         Show Agent health and safe attestation/SPIFFE ID summary
  watch          Follow the Agent log and print high-level progress
  server-status  Query the authoritative Agent SVID record on the SPIRE Server

Required for preflight/run:
  ARGUS_POLICY_NOT_AFTER=<RFC3339 timestamp>

Optional overrides:
  ARGUS_AGENT_BINARY, ARGUS_AGENT_CONFIG, ARGUS_AGENT_LOG,
  ARGUS_AGENT_PID_FILE, ARGUS_AGENT_ID, ARGUS_SERVER_BINARY,
  ARGUS_SERVER_SOCKET, ARGUS_TRANSPORT_TIMEOUT
EOF
}

case "$ACTION" in
    preflight) preflight ;;
    run) run_agent ;;
    status) status ;;
    watch) watch_log ;;
    server-status) server_status ;;
    help | -h | --help) usage ;;
    *) usage >&2; fail "unknown action: $ACTION" ;;
esac
