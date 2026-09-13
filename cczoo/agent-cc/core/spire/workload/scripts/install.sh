#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ARGUS_WORKLOAD_BUILD_DIR:-$ROOT/build}"
[[ "$EUID" == 0 ]] || { echo "run install as root" >&2; exit 1; }
[[ -f "$OUT/SHA256SUMS" ]] || { echo "run build.sh first" >&2; exit 1; }
source "$ROOT/scripts/build-artifacts.sh"
for artifact in "${ARGUS_WORKLOAD_ARTIFACTS[@]}"; do
    [[ -f "$OUT/$artifact" && -x "$OUT/$artifact" ]] || { echo "missing executable build artifact: $artifact; run build.sh first" >&2; exit 1; }
done
# Compare the complete current payload, including official SPIRE executables.
# An older or incomplete manifest cannot authorize a newer, unlisted binary.
expected_checksums="$(cd "$OUT" && sha256sum -- "${ARGUS_WORKLOAD_ARTIFACTS[@]}")"
[[ "$(cat "$OUT/SHA256SUMS")" == "$expected_checksums" ]] || { echo "build manifest is stale, incomplete, or contains changed artifacts; run build.sh first" >&2; exit 1; }
for tool in python3 nginx nsenter docker systemctl openssl timeout; do
    command -v "$tool" >/dev/null || { echo "missing command: $tool" >&2; exit 1; }
done
nginx -V 2>&1 | grep -q -- --with-http_auth_request_module || { echo "NGINX auth_request module required" >&2; exit 1; }
getent group argus-nginx >/dev/null || groupadd --system argus-nginx
id argus-nginx >/dev/null 2>&1 || useradd --system --gid argus-nginx --no-create-home --shell /usr/sbin/nologin argus-nginx
install -d -m 0755 /opt/argus-workload/bin /opt/argus-workload/scripts /opt/argus-workload/config /opt/argus-workload/policy /opt/argus-workload/systemd /opt/spire-1.15.3/bin
install -d -m 0700 /etc/argus-workload /run/argus-workload /var/log/argus-workload
for artifact in "${ARGUS_WORKLOAD_ARTIFACTS[@]}"; do
    case "$artifact" in
        bin/*) install -m 0755 "$OUT/$artifact" /opt/argus-workload/bin/ ;;
        spire-1.15.3/bin/*) install -m 0755 "$OUT/$artifact" /opt/spire-1.15.3/bin/ ;;
    esac
done
install -m 0755 "$ROOT/scripts/nginx-hook.sh" /opt/argus-workload/bin/
install -m 0755 "$ROOT/scripts/workload.py" /opt/argus-workload/scripts/
install -m 0755 "$ROOT/scripts/verify-lifecycle.py" /opt/argus-workload/scripts/
install -m 0755 "$ROOT/scripts/watch-attestation.py" /opt/argus-workload/scripts/
install -m 0644 "$ROOT"/config/* /opt/argus-workload/config/
install -m 0644 "$ROOT"/policy/* /opt/argus-workload/policy/
install -m 0644 "$ROOT"/systemd/* /opt/argus-workload/systemd/
install -m 0644 "$ROOT"/systemd/* /etc/systemd/system/
systemctl daemon-reload
printf 'INSTALL=PASS\nCONFIG=/etc/argus-workload/environment.json\n'
