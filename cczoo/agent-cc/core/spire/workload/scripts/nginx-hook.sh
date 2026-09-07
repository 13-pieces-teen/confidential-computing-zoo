#!/usr/bin/env bash
set -euo pipefail
case "${1:?action required}" in
  publish)
    install -d -m 0755 /run/argus-nginx
    /usr/sbin/nginx -t -c /etc/argus-workload/nginx.conf
    if systemctl is-active --quiet argus-nginx.service; then
      systemctl reload argus-nginx.service
    else
      systemctl start argus-nginx.service
    fi
    systemctl is-active --quiet argus-nginx.service
    target_pid="$(/opt/argus-workload/bin/argus-workload -action check | python3 -c 'import json,sys;print(json.load(sys.stdin)["pid"])')"
    nsenter --target "$target_pid" --net /opt/argus-workload/bin/spiffe-mtls-probe \
      -tls-only -url https://127.0.0.1:1943 \
      -cert /run/argus-credentials/current/svid.pem -key /run/argus-credentials/current/key.pem \
      -bundle /run/argus-credentials/current/bundle.pem
    ;;
  reload)
    /usr/sbin/nginx -t -c /etc/argus-workload/nginx.conf
    /usr/sbin/nginx -s reload -c /etc/argus-workload/nginx.conf
    ;;
  exec)
    target_pid="$(/opt/argus-workload/bin/argus-workload -action check | python3 -c 'import json,sys;print(json.load(sys.stdin)["pid"])')"
    exec nsenter --target "$target_pid" --net /usr/sbin/nginx -c /etc/argus-workload/nginx.conf -g 'daemon off;'
    ;;
  stop)
    systemctl stop argus-nginx.service
    ;;
  clear)
    # Fixed private tmpfs directory; used after an uncatchable Helper crash.
    python3 - <<'PY'
from pathlib import Path
import shutil
p=Path("/run/argus-credentials")
if p.is_symlink():
    raise SystemExit("credential directory must not be a symlink")
if p.exists():
    for child in p.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.name.startswith("generation-"):
            shutil.rmtree(child)
PY
    ;;
  *) echo "unknown NGINX hook action" >&2; exit 1 ;;
esac
