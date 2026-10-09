#!/usr/bin/env bash
# Bounded, detached sampling loop around e1-business-probe.sh (E1 Full A, new attempt).
# Serial, non-overlapping rounds with 5s gap, max DURATION seconds. No LLM. Keys stay on guest.
# Usage: sudo bash /root/e1/e1-sample-loop.sh <prefix> <duration_s> <outdir>
set -u
PREFIX="${1:?prefix}"
DUR="${2:-900}"
OUTDIR="${3:-/root/argus-openclaw-evidence/e1-20261004/samples}"
PROBEJSONL=/root/argus-openclaw-evidence/e1-probes/probe.jsonl
mkdir -p "$OUTDIR"
ROUNDS="$OUTDIR/rounds.jsonl"
echo "$$" > "$OUTDIR/loop.pid"
echo "{\"event\":\"loop_start\",\"prefix\":\"$PREFIX\",\"duration_s\":$DUR,\"pid\":$$,\"started_at\":\"$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)\"}" > "$OUTDIR/loop-status.jsonl"
DEADLINE=$(( $(date +%s) + DUR ))
SEQ=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  SEQ=$((SEQ+1))
  RID="$(cat /proc/sys/kernel/random/uuid)"
  ST="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
  LABEL="${PREFIX}-${SEQ}"
  bash /root/e1/e1-business-probe.sh "$LABEL" >/dev/null 2>&1
  RC=$?
  EN="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
  LAST="$(tail -1 "$PROBEJSONL" 2>/dev/null)"
  python3 - "$ROUNDS" "$SEQ" "$RID" "$ST" "$EN" "$RC" "$LAST" "$LABEL" <<'PYEOF'
import json, sys
out, seq, rid, st, en, rc, last, label = sys.argv[1:9]
legs = {}
try:
    d = json.loads(last)
    if d.get("label") == label:
        for k in ("mtls_health", "gateway_sessions", "gateway_search"):
            try:
                v = d.get(k) or {}
                legs[k] = {"http_status": v.get("http_status"), "result": v.get("result")}
            except Exception:
                legs[k] = None
except Exception:
    pass
rec = {"round": int(seq), "round_id": rid, "label": label,
       "started_at": st, "ended_at": en, "probe_rc": int(rc), "legs": legs}
open(out, "a").write(json.dumps(rec) + "\n")
PYEOF
  sleep 5
done
echo "{\"event\":\"loop_end\",\"seq\":$SEQ,\"ended_at\":\"$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)\"}" >> "$OUTDIR/loop-status.jsonl"
