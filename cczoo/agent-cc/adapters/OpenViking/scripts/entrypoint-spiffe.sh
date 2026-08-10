#!/usr/bin/env bash
set -euo pipefail

if [[ "${ARGUS_SPIFFE_ENABLED:-0}" != "1" ]]; then
    exec openviking-server "$@"
fi

: "${SPIFFE_ENDPOINT_SOCKET:?SPIFFE_ENDPOINT_SOCKET is required}"
: "${ARGUS_WORKLOAD_SPIFFE_ID:?ARGUS_WORKLOAD_SPIFFE_ID is required}"
: "${ARGUS_EXPECTED_CLIENT_SPIFFE_ID:?ARGUS_EXPECTED_CLIENT_SPIFFE_ID is required}"

credential_dir="${ARGUS_SPIFFE_CREDENTIAL_DIR:-/run/argus-svid}"
mkdir -p "$credential_dir"
chmod 0700 "$credential_dir"

/usr/local/bin/argus-svid-materializer \
    -socket "$SPIFFE_ENDPOINT_SOCKET" \
    -spiffe-id "$ARGUS_WORKLOAD_SPIFFE_ID" \
    -output-dir "$credential_dir" &
materializer_pid=$!
server_pid=""

stop_children() {
    [[ -z "$server_pid" ]] || kill -TERM "$server_pid" >/dev/null 2>&1 || true
    kill -TERM "$materializer_pid" >/dev/null 2>&1 || true
    [[ -z "$server_pid" ]] || wait "$server_pid" >/dev/null 2>&1 || true
    wait "$materializer_pid" >/dev/null 2>&1 || true
}
trap stop_children EXIT INT TERM

ready_timeout="${ARGUS_SPIFFE_READY_TIMEOUT_SECONDS:-30}"
for ((attempt=0; attempt<ready_timeout; attempt++)); do
    [[ -s "$credential_dir/status.json" ]] && break
    kill -0 "$materializer_pid" 2>/dev/null || { wait "$materializer_pid"; exit $?; }
    sleep 1
done
[[ -s "$credential_dir/status.json" ]] \
    || { echo "OpenViking SPIFFE credentials were not ready after ${ready_timeout}s" >&2; exit 1; }

export ARGUS_SPIFFE_CREDENTIAL_DIR="$credential_dir"
python3 -m spiffe_server.server "$@" &
server_pid=$!
set +e
wait -n "$materializer_pid" "$server_pid"
status=$?
set -e
echo 'OpenViking server or SPIFFE credential watcher exited; stopping the workload.' >&2
exit "$status"
