#!/usr/bin/env bash
# E1 checkpoint probe wrapper (IP1 client guest). Runs the deterministic business probe
# with live credentials for one checkpoint, saves per-checkpoint metadata. No LLM.
# Usage: sudo bash e1-checkpoint-probe.sh <stage> <attempt_id>
set -u
STAGE="${1:?stage A|B|C}"; ATTEMPT="${2:?attempt_id}"
TS="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
RD=/run/argus-openclaw/instances/paper01full/credentials
OUTDIR=/root/argus-openclaw-evidence/e1-20261004/checkpoints
mkdir -p "$OUTDIR"
GEN="$(python3 -c 'import json;print(json.load(open("'"$RD"'/ready.json"))["generation"])' 2>/dev/null)"
SER="$(openssl x509 -in "$RD/$GEN/svid.pem" -noout -serial 2>/dev/null | cut -d= -f2)"
EXP="$(openssl x509 -in "$RD/$GEN/svid.pem" -noout -enddate 2>/dev/null | cut -d= -f2)"
TGT="$(python3 -c 'import json;print(json.dumps(json.load(open("/run/argus-openclaw/instances/paper01full/target.json")),sort_keys=True))' 2>/dev/null)"
bash /root/e1/e1-business-probe.sh "e1-full-20261004-${STAGE}-${ATTEMPT}" > /dev/null 2>&1
PRC=$?
LAST="$(tail -1 /root/argus-openclaw-evidence/e1-probes/probe.jsonl 2>/dev/null)"
python3 - "$STAGE" "$ATTEMPT" "$TS" "$GEN" "$SER" "$EXP" "$TGT" "$PRC" "$LAST" <<'PYEOF'
import json, sys
stage, attempt, ts, gen, ser, exp, tgt, prc, last = sys.argv[1:10]
rec = {
    "schema": "argus.e1-checkpoint-probe.v1",
    "run_id": "e1-full-20261004",
    "stage": stage, "attempt_id": attempt,
    "observed_at": ts,
    "live_credential": {"generation": gen, "serial": ser, "not_after": exp},
    "target": json.loads(tgt) if tgt else None,
    "business_probe_rc": int(prc),
    "business_probe_record": json.loads(last) if last else None,
}
out = f"/root/argus-openclaw-evidence/e1-20261004/checkpoints/{stage}-{attempt}.json"
open(out, "w").write(json.dumps(rec, indent=1, sort_keys=True) + "\n")
print("checkpoint saved:", out, "gen:", gen, "probe_rc:", prc)
PYEOF
