#!/usr/bin/env bash
# Apply WP2 data-plane tightening on the running deployment:
#   1) ensure the argus-docker-gate proxy is running,
#   2) repoint the OpenClaw gateway Docker socket to the proxy,
#   3) rebuild the egress bridge as --internal and reconnect the gateway,
#   4) recreate the egress container and wait for its SVID.
# Run as root on the remote host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${V2_RUNTIME_DIR:=/var/lib/argus-spire-v2-runtimes/pre-ra-wp1-20260806T013248Z}"
export V2_RUNTIME_DIR

echo "=== [1/4] Ensure argus-docker-gate proxy is running ==="
"$SCRIPT_DIR/start-docker-gate.sh"

echo "=== [2/4] Repoint the OpenClaw gateway Docker socket to the proxy ==="
"$SCRIPT_DIR/repoint-openclaw-socket.sh"

echo "=== [3/4] Egress bridge: ensure --internal and only the gateway attached ==="
python3 - "$SCRIPT_DIR" <<'PY'
import json
import subprocess
import sys

script_dir = sys.argv[1]
egress_network = "argus-openclaw-egress"
gateway = "agentcc-openclaw-sbx-gateway"
subnet = "172.31.44.0/28"
gateway_ip = "172.31.44.1"
egress_ip = "172.31.44.2"

def run(args):
    return subprocess.run(args, capture_output=True, text=True)

def network_exists():
    r = run(["docker", "network", "inspect", egress_network])
    return r.returncode == 0

def current_internal():
    r = run(["docker", "network", "inspect", egress_network, "--format", "{{.Internal}}"])
    return r.stdout.strip()

def gateway_attached():
    r = run(["docker", "inspect", gateway])
    if r.returncode != 0:
        return ""
    data = json.loads(r.stdout)[0]
    n = data.get("NetworkSettings", {}).get("Networks", {}).get(egress_network)
    return "" if n is None else n.get("IPAddress", "")

if network_exists() and current_internal() == "true":
    print(f"{egress_network} is already --internal")
else:
    ip = gateway_attached()
    if ip:
        print(f"disconnecting {gateway} from {egress_network}")
        run(["docker", "network", "disconnect", egress_network, gateway])
    if network_exists():
        run(["docker", "network", "rm", egress_network])
    r = run(["docker", "network", "create", "--internal", "--driver", "bridge",
             "--subnet", subnet, "--gateway", gateway_ip, egress_network])
    if r.returncode != 0:
        print("network create failed:", r.stderr)
        sys.exit(1)
    print(f"recreated {egress_network} with --internal")

# Attach the gateway at the fixed egress IP.
if gateway_attached() == egress_ip:
    print(f"{gateway} already attached as {egress_ip}")
else:
    r = run(["docker", "network", "connect", "--ip", egress_ip, egress_network, gateway])
    if r.returncode != 0:
        print("network connect failed:", r.stderr)
        sys.exit(1)
    print(f"attached {gateway} as {egress_ip}")

# No sibling containers may remain on the egress bridge.
r = run(["docker", "network", "inspect", egress_network, "--format",
         "{{range $k, $v := .Containers}}{{$v.Name}} {{end}}"])
members = r.stdout.split()
extra = [m for m in members if m != gateway]
if extra:
    print(f"ERROR: unexpected egress members: {extra}")
    sys.exit(1)
print("egress members ok:", members)
PY

echo "=== [4/4] Recreate the OpenClaw mTLS egress and wait for its SVID ==="
V2_OPENCLAW_PROXY_BIND="${V2_OPENCLAW_PROXY_BIND:-172.31.44.1}"
V2_OPENCLAW_EGRESS_IP="${V2_OPENCLAW_EGRESS_IP:-172.31.44.2}"
V2_MTLS_RUNTIME_IMAGE="${V2_MTLS_RUNTIME_IMAGE:-$(docker image inspect argus-spire-v2-mtls:local --format '{{.Id}}')}"
export V2_OPENCLAW_PROXY_BIND V2_OPENCLAW_EGRESS_IP V2_MTLS_RUNTIME_IMAGE
docker compose -f "$SCRIPT_DIR/compose.center.yaml" \
    --profile workload \
    up -d --force-recreate --no-deps openclaw-mtls-client

for _ in $(seq 1 30); do
    if docker compose -f "$SCRIPT_DIR/compose.center.yaml" \
        --profile workload \
        exec -T openclaw-mtls-client \
        /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expected-id=spiffe://argus.local/agent/openclaw >/dev/null 2>&1; then
        echo "OpenClaw mTLS egress is ready."
        exit 0
    fi
    sleep 1
done

echo "OpenClaw mTLS egress did not receive its X.509-SVID." >&2
exit 1
