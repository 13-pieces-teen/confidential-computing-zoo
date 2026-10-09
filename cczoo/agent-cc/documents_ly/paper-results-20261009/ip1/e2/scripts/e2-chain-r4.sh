#!/bin/bash
# E2 r4 standing-arm chain (IP2 authorized 20261008): sync -> config -> arm -> launch.
# Contract: sync before arm, never after; zero steps between arm success and launch.
set -u
BASE=/root/argus-e2-ip1
CRED=$BASE/credentials
CONF=$BASE/e2-config-r4.json
RECEIPT=/var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/publish-hold-standing2.json
ARM_CMD="python3 /opt/argus-experiments/paper02/full_argus/payload/scripts/e2_helper_publish_hold.py arm --receipt $RECEIPT"
LOG=$BASE/e2-chain-r4.log

echo "=== chain start $(date -u +%FT%TZ) ===" >> "$LOG"

# safety guards: exactly one run, fresh output dir, no stray coordinator
# (bracket form so the guard never matches its own command line)
if [ -e "$BASE/output-r4" ]; then echo "ABORT: output-r4 already exists" >> "$LOG"; exit 1; fi
if pgrep -f "fault_trial[.]py run" >/dev/null 2>&1; then echo "ABORT: coordinator already running" >> "$LOG"; exit 1; fi

sync_creds() {
  ssh -p 2224 -i /root/.ssh/id_ed25519 -o BatchMode=yes tdx@127.0.0.1 \
    "sudo tar -czf - -C /run/argus-openclaw/instances/paper01full/credentials ." 2>>"$LOG" \
    | tar -xzf - -C "$CRED"
}

client_left() {
  local gen cert na
  gen=$(python3 -c "import json;print(json.load(open('$CRED/ready.json'))['generation'])" 2>/dev/null) || { echo 0; return; }
  cert=$CRED/$gen/svid.pem
  [ -f "$cert" ] || { echo 0; return; }
  na=$(openssl x509 -in "$cert" -noout -enddate 2>/dev/null | cut -d= -f2)
  [ -n "$na" ] || { echo 0; return; }
  echo $(( $(date -u -d "$na" +%s) - $(date -u +%s) ))
}

# --- step 1: fresh client credential (contract >=150s; target >=180s) ---
LEFT=0; GEN=""
for i in $(seq 1 25); do
  sync_creds
  LEFT=$(client_left)
  GEN=$(python3 -c "import json;print(json.load(open('$CRED/ready.json'))['generation'])" 2>/dev/null || echo "")
  echo "sync#$i gen=$GEN left=${LEFT}s $(date -u +%FT%TZ)" >> "$LOG"
  if [ "${LEFT:-0}" -ge 180 ]; then break; fi
  sleep 12
done
if [ "${LEFT:-0}" -lt 180 ]; then
  echo "GIVE UP: client credential stale (left=${LEFT}s); no arm performed" >> "$LOG"
  exit 1
fi

# --- step 2: point config at the fresh generation (svid.pem layout, as r3) ---
python3 - "$GEN" <<'PYEOF'
import json, sys
gen = sys.argv[1]
p = '/root/argus-e2-ip1/e2-config-r4.json'
c = json.load(open(p))
c['probe']['cert'] = f'/root/argus-e2-ip1/credentials/{gen}/svid.pem'
c['probe']['key']  = f'/root/argus-e2-ip1/credentials/{gen}/key.pem'
c['probe']['bundle'] = f'/root/argus-e2-ip1/credentials/{gen}/bundle.pem'
json.dump(c, open(p, 'w'), indent=2)
print('config updated to', gen)
PYEOF

# --- step 3: arm (retry on refuse; re-sync before each retry; never re-sync after success) ---
# Success criterion: receipt exists with state==armed (receipt schema has NO ready field).
# Peek the receipt BEFORE sending arm so a re-entry after an interrupted chain
# never sends a second arm command (single-arm constraint).
ARMED=0
for a in $(seq 1 6); do
  echo "=== arm attempt $a $(date -u +%FT%TZ) ===" >> "$LOG"
  RCPT=$(ssh -o BatchMode=yes argus-ip2 "cat $RECEIPT 2>/dev/null" 2>/dev/null || true)
  STATE=$(printf '%s' "$RCPT" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('state',''))
except Exception:
    print('')" 2>/dev/null)
  if [ "$STATE" = "armed" ]; then
    echo "ARM ALREADY ARMED (peek); no second arm sent $(date -u +%FT%TZ)" >> "$LOG"
    ARMED=1
    break
  fi
  ARM_OUT=$(ssh -o BatchMode=yes argus-ip2 "$ARM_CMD" 2>&1)
  echo "$ARM_OUT" >> "$LOG"
  RCPT=$(ssh -o BatchMode=yes argus-ip2 "cat $RECEIPT 2>/dev/null" 2>/dev/null || true)
  STATE=$(printf '%s' "$RCPT" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('state',''))
except Exception:
    print('')" 2>/dev/null)
  if [ "$STATE" = "armed" ]; then
    ARMED=1
    echo "ARM SUCCESS state=$STATE $(date -u +%FT%TZ)" >> "$LOG"
    break
  fi
  echo "arm refused (state=${STATE:-missing}); waiting for next server publication" >> "$LOG"
  sleep 100
  sync_creds
  LEFT=$(client_left)
  if [ "${LEFT:-0}" -lt 150 ]; then
    for j in $(seq 1 20); do
      sync_creds; LEFT=$(client_left)
      [ "${LEFT:-0}" -ge 150 ] && break
      sleep 12
    done
  fi
done
if [ "$ARMED" != "1" ]; then
  echo "GIVE UP: arm did not succeed within retries; no coordinator started" >> "$LOG"
  exit 1
fi

# --- step 4: launch the single coordinator immediately, zero intermediate steps ---
if [ -e "$BASE/output-r4" ] || pgrep -f "fault_trial[.]py run" >/dev/null 2>&1; then
  echo "ABORT at launch: output-r4 exists or coordinator running; no launch" >> "$LOG"
  exit 1
fi
export ARGUS_SYNTHETIC_USER_KEY=$(cat /root/argus-ip1-handoff/probe-round6-403-20261008/ip2-handoff/paper02-user-api-key)
cd /home/ying_liu/confidential-computing-zoo/cczoo/agent-cc
setsid python3.11 experiments/argus/fault_trial.py run \
  --config "$CONF" \
  --output "$BASE/output-r4" \
  > "$BASE/output-r4-run.log" 2>&1 < /dev/null &
COORD_PID=$!
echo "LAUNCHED coordinator pid=$COORD_PID $(date -u +%FT%TZ)" >> "$LOG"
exit 0
