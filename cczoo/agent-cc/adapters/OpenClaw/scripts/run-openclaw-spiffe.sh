#!/usr/bin/env bash
set -euo pipefail

credential_dir="${ARGUS_SPIFFE_CREDENTIAL_DIR:-/run/argus-svid}"
/usr/local/bin/argus-svid-materializer \
  -socket "$SPIFFE_ENDPOINT_SOCKET" \
  -spiffe-id "$ARGUS_CALLER_SPIFFE_ID" \
  -output-dir "$credential_dir" &
materializer_pid=$!
gateway_pid=""

stop_children() {
  [[ -z "$gateway_pid" ]] || kill -TERM "$gateway_pid" >/dev/null 2>&1 || true
  kill -TERM "$materializer_pid" >/dev/null 2>&1 || true
  [[ -z "$gateway_pid" ]] || wait "$gateway_pid" >/dev/null 2>&1 || true
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
  || { echo "ERROR: SPIFFE credentials were not ready after ${ready_timeout}s" >&2; exit 1; }

node /app/dist/index.js gateway --bind "$1" --port "$2" &
gateway_pid=$!
set +e
wait -n "$materializer_pid" "$gateway_pid"
status=$?
set -e
echo "ERROR: OpenClaw gateway or SPIFFE credential watcher exited; stopping the workload" >&2
exit "$status"
