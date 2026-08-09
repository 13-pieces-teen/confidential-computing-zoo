#!/usr/bin/env bash
# Recreate the real OpenClaw gateway so its Docker socket is the argus-docker-gate
# proxy instead of the raw daemon socket (WP2 Docker control-plane isolation).
# All other container settings (mounts, env, ports, group-add, restart, init)
# and additional networks are preserved.
set -euo pipefail

CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
PROXY_SOCKET="${V2_DOCKER_GATE_SOCKET:-/var/run/argus/docker-proxy.sock}"

fail() {
    printf 'repoint socket: FAIL: %s\n' "$1" >&2
    exit 1
}

docker inspect "$CONTAINER" >/dev/null 2>&1 \
    || fail "gateway container does not exist: $CONTAINER"
[[ -S "$PROXY_SOCKET" ]] \
    || fail "proxy socket not found at $PROXY_SOCKET"

# Confirm the current socket mount would actually change; otherwise this is a no-op.
current_socket_source="$(
    docker inspect "$CONTAINER" | python3 -c '
import json, sys
c = json.load(sys.stdin)[0]
for m in c.get("Mounts") or []:
    if m.get("Destination") == "/var/run/docker.sock":
        print(m.get("Source", ""))
        break
'
)"
if [[ "$current_socket_source" == "$PROXY_SOCKET" ]]; then
    echo "gateway already uses the proxy socket; no change needed."
    exit 0
fi

# Capture the additional networks (everything except the main network mode).
main_network="$(docker inspect "$CONTAINER" --format '{{.HostConfig.NetworkMode}}')"
additional_networks="$(
    docker inspect "$CONTAINER" | python3 -c '
import json, sys
c = json.load(sys.stdin)[0]
main = sys.argv[1]
for name, cfg in (c.get("NetworkSettings", {}).get("Networks", {}) or {}).items():
    if name == main:
        continue
    print(name, cfg.get("IPAddress", ""))
' "$main_network"
)"

# Generate the docker run command preserving config, swapping the socket.
run_args="$(
    docker inspect "$CONTAINER" | PROXY_SOCKET="$PROXY_SOCKET" python3 -c '
import json, os, shlex, subprocess, sys
c = json.load(sys.stdin)[0]
cfg = c.get("Config", {})
h = c.get("HostConfig", {})
proxy = os.environ["PROXY_SOCKET"]
args = ["docker", "run", "-d", "--name", sys.argv[1]]
if h.get("Init"):
    args.append("--init")
args += ["--restart", (h.get("RestartPolicy") or {}).get("Name", "no")]
for g in h.get("GroupAdd") or []:
    args += ["--group-add", str(g)]
for m in c.get("Mounts") or []:
    dst = m.get("Destination")
    src = m.get("Source")
    typ = m.get("Type")
    rw = m.get("RW", True)
    if dst == "/var/run/docker.sock":
        src = proxy
        rw = True
    opt = "" if rw else ":ro"
    if typ == "bind" or typ == "volume":
        args += ["-v", f"{src}:{dst}{opt}"]
for e in cfg.get("Env") or []:
    args += ["-e", e]
for cport, binds in (h.get("PortBindings") or {}).items():
    for b in binds or []:
        hp = b.get("HostPort", "")
        hip = b.get("HostIp", "")
        if hip:
            args += ["-p", f"{hip}:{hp}:{cport}"]
        else:
            args += ["-p", f"{hp}:{cport}"]
nm = h.get("NetworkMode", "default")
if nm:
    args += ["--network", nm]
args.append(cfg.get("Image", ""))
print(" ".join(shlex.quote(a) for a in args))
' "$CONTAINER"
)"
# Remove the old gateway container before recreating it with the proxy socket.
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
eval "$run_args" >/dev/null || fail "docker run of the gateway failed"

# Reconnect the additional networks with their previous IPs.
if [[ -n "$additional_networks" ]]; then
    printf '%s\n' "$additional_networks" | while read -r net ip; do
        [[ -n "$net" ]] || continue
        if [[ -n "$ip" ]]; then
            docker network connect --ip "$ip" "$net" "$CONTAINER"
        else
            docker network connect "$net" "$CONTAINER"
        fi
    done
fi

echo "gateway recreated with proxy socket: $PROXY_SOCKET -> /var/run/docker.sock"
echo "additional networks restored: ${additional_networks:-none}"
