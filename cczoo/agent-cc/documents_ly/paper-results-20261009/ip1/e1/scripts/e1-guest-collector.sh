#!/usr/bin/env bash
# E1 guest-side evidence collectors: gateway container logs + credential rotation watcher.
set -u
OUT=/root/argus-openclaw-evidence/e1-probes
mkdir -p "$OUT"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "=== collector started $TS ===" >> "$OUT/gateway-follow.log"
nohup docker logs -f --since 1m argus-oc-paper01full >> "$OUT/gateway-follow.log" 2>&1 &
echo $! > "$OUT/gateway-follow.pid"
echo "=== rotation watcher started $TS ===" >> "$OUT/rotation-watch.jsonl"
nohup bash -c 'while true; do gen=$(cat /run/argus-openclaw/instances/paper01full/credentials/ready.json 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)[\"generation\"])" 2>/dev/null); now=$(date -u +%Y-%m-%dT%H:%M:%SZ); [ -n "$gen" ] && echo "{\"at\":\"$now\",\"generation\":\"$gen\"}" >> /root/argus-openclaw-evidence/e1-probes/rotation-watch.jsonl; sleep 5; done' >> "$OUT/rotation-watcher.log" 2>&1 &
echo $! > "$OUT/rotation-watch.pid"
echo "guest collectors started"
