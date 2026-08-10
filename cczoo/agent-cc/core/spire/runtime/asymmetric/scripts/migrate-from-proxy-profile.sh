#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-plan}"
OLD_OPENCLAW_PROXY="${V2_OLD_OPENCLAW_PROXY_CONTAINER:-argus-v2-openclaw-mtls}"
OLD_OPENVIKING_PROXY="${V2_OLD_OPENVIKING_PROXY_CONTAINER:-argus-v2-openviking-mtls}"

[[ "$ACTION" == plan || "$ACTION" == apply ]] \
    || { echo 'usage: migrate-from-proxy-profile.sh [plan|apply]' >&2; exit 1; }

inspect_container() {
    local name="$1"
    if docker inspect "$name" >/dev/null 2>&1; then
        docker inspect "$name" --format 'container={{.Name}} image={{.Config.Image}} status={{.State.Status}} labels={{json .Config.Labels}}'
    else
        printf 'container=/%s status=absent\n' "$name"
    fi
}

echo 'Legacy proxy-profile inventory:'
inspect_container "$OLD_OPENCLAW_PROXY"
inspect_container "$OLD_OPENVIKING_PROXY"
echo 'Persistent OpenClaw/OpenViking volumes and images are outside this migration scope.'

if [[ "$ACTION" == plan ]]; then
    echo 'Plan only: rerun with apply to remove exactly the two legacy proxy containers above.'
    exit 0
fi

for container in "$OLD_OPENCLAW_PROXY" "$OLD_OPENVIKING_PROXY"; do
    if docker inspect "$container" >/dev/null 2>&1; then
        docker rm -f "$container" >/dev/null
        printf 'Removed legacy proxy container: %s\n' "$container"
    fi
done

printf '%s\n' \
    'Legacy business-path proxies are removed.' \
    'No volume, image, SPIRE data directory, or application state was removed.' \
    'Continue with start-openclaw-workload.sh and the native OpenViking launcher.'
