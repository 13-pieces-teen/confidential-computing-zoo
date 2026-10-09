#!/bin/bash
# Watch for IP2 attempt metadata until A3 sampling window expires (19:49:46.437Z).
DEADLINE=$(date -u -d '2026-10-03T19:49:46Z' +%s)
LOCALDIR=/var/lib/argus-review/e1-20261004/ip2-handoff
LOG=/var/lib/argus-review/e1-20261004/ip2-handoff-watch-a3.log
SSH=(ssh -o 'ProxyCommand=/usr/bin/ncat --proxy proxy-dmz.intel.com:912 --proxy-type http %h %p' -o BatchMode=yes -o ConnectTimeout=15 root@115.190.62.46)
PREV=/tmp/a3-watch-prev.txt
: > "$PREV"
echo "A3 watch start $(date -u +%Y-%m-%dT%H:%M:%SZ) deadline $DEADLINE" > "$LOG"
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  SNAP="/tmp/a3-watch-snap.txt"
  { find "$LOCALDIR" -type f -printf 'L %p %s %T@\n' 2>/dev/null | sort; \
    "${SSH[@]}" "find /root/argus-ip2-handoff -maxdepth 3 -type f -printf 'R %p %s %T@\n' 2>/dev/null | sort" 2>/dev/null; } > "$SNAP"
  if ! diff -q "$PREV" "$SNAP" >/dev/null 2>&1; then
    echo "=== change at $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "$LOG"
    diff "$PREV" "$SNAP" | head -40 >> "$LOG"
    cp "$SNAP" "$PREV"
  fi
  sleep 20
done
echo "A3 watch EXPIRED $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"
