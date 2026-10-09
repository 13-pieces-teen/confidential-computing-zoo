#!/usr/bin/env python3
"""Offline recomputation of the frozen S1 client trace (paper-final addendum).

Reads the frozen original requests.jsonl (sha f490611c...) and produces an
independent breakdown. Never modifies the original file.
"""
import json
import sys
from collections import Counter, OrderedDict

SRC = "/secure/paper-minimal-20261009/rotation/argus-paper-minimal-20261009t0718z-01-rotation/requests.jsonl"

OBS_START = 1791531867266   # IP2 started_at_ms (OBSERVATION-FINISHED)
OBS_END = 1791532468115     # IP2 completed_at_ms
COLL_START = OBS_START - 20000  # clock-uncertainty bound
COLL_END = OBS_END + 20000

rows = []
with open(SRC) as f:
    for i, line in enumerate(f, 1):
        rows.append((i, json.loads(line)))

def val(d, k):
    return d.get(k)

warmup = [r for r in rows if r[1].get("phase") == "warmup"]
meas = [r for r in rows if r[1].get("phase") != "warmup"]

print("total rows:", len(rows), "warmup:", len(warmup), "measurement:", len(meas))

outcomes = Counter(r.get("outcome") for _, r in meas)
print("measurement outcomes:", dict(outcomes))

err = Counter()
for _, r in meas:
    if r.get("outcome") != "success":
        err[(r.get("error_class"), str(r.get("http_status")), r.get("submission_state"))] += 1
print("non-success breakdown (error_class, http_status, submission_state):")
for k, v in sorted(err.items(), key=lambda x: -x[1]):
    print("  ", v, k)

# distinct peer serials over the whole file
serials = OrderedDict()
for _, r in rows:
    s = r.get("peer_svid_serial")
    if s:
        serials.setdefault(s, r.get("started_at_ms"))
print("distinct peer serials:", len(serials))

# memory_result distribution among successes (measurement only)
mr = Counter(r.get("memory_result") for _, r in meas if r.get("outcome") == "success")
print("success memory_result:", dict(mr))

# in-window placement of non-success rows
in_win = []
for idx, r in meas:
    if r.get("outcome") == "success":
        continue
    t = r.get("started_at_ms")
    try:
        t = int(float(t))
    except (TypeError, ValueError):
        t = None
    if t is not None and COLL_START <= t <= COLL_END:
        in_win.append((idx, r))
print("non-success rows inside collect window [+-20s]:", len(in_win),
      "of", sum(err.values()))

# class of in-window unknowns
inw_err = Counter(r.get("error_class") for _, r in in_win)
print("in-window non-success error_class:", dict(inw_err))

# class of out-of-window unknowns
out_win = [(idx, r) for idx, r in meas
           if r.get("outcome") != "success"
           and not (r.get("started_at_ms") is not None
                    and COLL_START <= int(float(r.get("started_at_ms"))) <= COLL_END)]
outw_err = Counter(r.get("error_class") for _, r in out_win)
print("out-of-window non-success error_class:", dict(outw_err))
print("out-of-window rows (idx, started_at_ms, error_class, http_status, submission_state):")
for idx, r in out_win:
    print("  row", idx, r.get("started_at_ms"), r.get("error_class"),
          r.get("http_status"), r.get("submission_state"), r.get("error_message", "")[:60])

# final episode: trailing consecutive non-success rows
last = None
for idx, r in meas:
    if r.get("outcome") == "success":
        last = None
    else:
        if last is None:
            last = []
        last.append(idx)
print("final trailing non-success episode length:", len(last) if last else 0,
      "first row idx:", last[0] if last else None)
if last:
    for idx in last:
        r = dict(meas)[idx]
        print("   row", idx, "started_at_ms", r.get("started_at_ms"), "error_class", r.get("error_class"))

# intermediate blips: single non-success between successes
blips = []
prev_succ = False
cur = []
for idx, r in meas:
    if r.get("outcome") != "success":
        cur.append(idx)
        prev_succ = False
    else:
        if cur and len(cur) <= 2:
            blips.append(list(cur))
        cur = []
        prev_succ = True
if cur and len(cur) <= 2:
    blips.append(list(cur))
print("short non-success episodes (<=2 rows, i.e. blips):", len(blips), blips)

# success http status check
hs = Counter(str(r.get("http_status")) for _, r in meas if r.get("outcome") == "success")
print("success http_status:", dict(hs))

# in-window RemoteDisconnected rows (positions)
print("in-window RemoteDisconnected rows:")
for idx, r in meas:
    t = r.get("started_at_ms")
    try:
        t = int(float(t))
    except (TypeError, ValueError):
        t = None
    if r.get("error_class") == "RemoteDisconnected" and t is not None and COLL_START <= t <= COLL_END:
        print("   row", idx, "started_at_ms", r.get("started_at_ms"),
              "submission_state", r.get("submission_state"),
              "error_message", r.get("error_message", "")[:80])

# in-window blip classes
print("in-window blip rows (idx, error_class, started_at_ms):")
for idx, r in meas:
    t = r.get("started_at_ms")
    try:
        t = int(float(t))
    except (TypeError, ValueError):
        t = None
    if r.get("outcome") != "success" and t is not None and COLL_START <= t <= COLL_END and idx not in last:
        print("   row", idx, r.get("error_class"), r.get("started_at_ms"),
              r.get("submission_state"))

# reconnects
rc = Counter(r.get("reconnect") for _, r in rows)
print("reconnect flags (all rows):", dict(rc))
rr = Counter(r.get("connect_reason") for _, r in rows if r.get("connect_reason") not in (None, "", "reused", "initial"))
print("connect reasons (non-reused):", dict(rr))

# load-result.json reconciliation
LR = "/secure/paper-minimal-20261009/rotation/argus-paper-minimal-20261009t0718z-01-rotation/load-result.json"
with open(LR) as f:
    lr = json.load(f)
def find_serial_fields(obj, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if "serial" in k.lower():
                out.append((p, str(v)[:120]))
            else:
                out.extend(find_serial_fields(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            out.extend(find_serial_fields(v, f"{path}[{i}]"))
    return out
for p, v in find_serial_fields(lr):
    print("load-result serial field:", p, "=", v)
