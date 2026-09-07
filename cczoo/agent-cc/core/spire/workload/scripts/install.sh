#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ARGUS_WORKLOAD_BUILD_DIR:-$ROOT/build}"
[[ "$EUID" == 0 ]] || { echo "run install as root" >&2; exit 1; }
[[ -f "$OUT/SHA256SUMS" ]] || { echo "run build.sh first" >&2; exit 1; }
(cd "$OUT" && sha256sum --check SHA256SUMS)
for tool in python3 nginx nsenter docker systemctl openssl timeout; do
    command -v "$tool" >/dev/null || { echo "missing command: $tool" >&2; exit 1; }
done
nginx -V 2>&1 | grep -q -- --with-http_auth_request_module || { echo "NGINX auth_request module required" >&2; exit 1; }
getent group argus-nginx >/dev/null || groupadd --system argus-nginx
id argus-nginx >/dev/null 2>&1 || useradd --system --gid argus-nginx --no-create-home --shell /usr/sbin/nologin argus-nginx
install -d -m 0755 /opt/argus-workload/bin /opt/argus-workload/scripts /opt/argus-workload/config /opt/argus-workload/policy /opt/argus-workload/systemd /opt/spire-1.15.3/bin
install -d -m 0700 /etc/argus-workload /run/argus-workload /var/log/argus-workload
install -m 0755 "$OUT"/bin/* /opt/argus-workload/bin/
install -m 0755 "$OUT"/spire-1.15.3/bin/* /opt/spire-1.15.3/bin/
install -m 0755 "$ROOT/scripts/nginx-hook.sh" /opt/argus-workload/bin/
install -m 0755 "$ROOT/scripts/workload.py" /opt/argus-workload/scripts/
install -m 0755 "$ROOT/scripts/verify-lifecycle.py" /opt/argus-workload/scripts/
install -m 0644 "$ROOT"/config/* /opt/argus-workload/config/
install -m 0644 "$ROOT"/policy/* /opt/argus-workload/policy/
install -m 0644 "$ROOT"/systemd/* /opt/argus-workload/systemd/
install -m 0644 "$ROOT"/systemd/* /etc/systemd/system/
systemctl daemon-reload
printf 'INSTALL=PASS\nCONFIG=/etc/argus-workload/environment.json\n'
