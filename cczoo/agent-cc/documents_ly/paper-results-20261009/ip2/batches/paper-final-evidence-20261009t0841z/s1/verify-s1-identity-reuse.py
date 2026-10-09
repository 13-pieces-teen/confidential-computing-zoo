#!/usr/bin/env python3
"""Re-derive the S1 citable conclusion from the minimal-batch server originals.

Reads only (no modification of originals). Confirms:
  - Provider counter interval start/end/duration
  - same Provider instance / socket / agent across the interval
  - same Helper subscription / unit / invocation for every publish record
  - same target launch/container across publishes
  - three distinct SVID serials inside the interval
  - Workload Quote attempted/generated/failed deltas 0/0/0
Keeps the business-continuity side separate and appends the IP1 corrections.
"""
import hashlib
import json
from pathlib import Path

SRV = Path("/root/argus-paper-minimal-20261009t0718z-01/server")
OUT = Path(__file__).resolve().parent

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def load(name):
    return json.loads((SRV / name).read_text())

before = load("provider-before.json")
after = load("provider-after.json")
journal = load("helper-journal-window.json")
anchor = load("helper-journal-anchor.json")

start, end = before["captured_at_ms"], after["captured_at_ms"]
duration = end - start  # ms

pubs = [r for r in journal["records"] if "target SVID published" in r["MESSAGE"]]
serials = sorted({r["MESSAGE"].split("serial=")[1].split()[0] for r in pubs})
pub_ms = [int(r["__REALTIME_TIMESTAMP"]) / 1000 for r in pubs]
in_interval = [ms >= start and ms <= end for ms in pub_ms]
same_subscription = len({r["_SYSTEMD_INVOCATION_ID"] for r in pubs}) == 1
same_unit = len({r["_SYSTEMD_UNIT"] for r in pubs}) == 1
same_target = len({
    (r["MESSAGE"].split("launch_id=")[1].split()[0],
     r["MESSAGE"].split("container_id=")[1].split()[0],
     r["MESSAGE"].split("pid=")[1].split()[0],
     r["MESSAGE"].split("start_time=")[1].split()[0],
     r["MESSAGE"].split("policy=")[1].split()[0])
    for r in pubs}) == 1
same_helper_pid = len({r["_PID"] for r in pubs}) == 1
same_boot = len({r["_BOOT_ID"] for r in pubs}) == 1

wq_delta = {k: after["workload"][k] - before["workload"][k]
            for k in ("attempted", "generated", "failed")}
node_delta = {k: after["node"][k] - before["node"][k]
              for k in ("attempted", "generated", "failed")}

# journal window must cover the counter interval
covered = (journal["coverage_started_at_ms"] <= start
           and end <= journal["coverage_ended_at_ms"])

# provider instance vs helper boot id (dashes only)
prov_boot = before["provider_instance_id"].split(":")[0]
boot = anchor["boot_id"]

# client freeze vs third publish (no complete client coverage claim)
client_end_ms = 1791532367347  # from RESULTS.csv business_max_probe_gap row
third_pub_ms = pub_ms[2]

result = {
    "schema": "argus.paper-final.s1-identity-reuse.v1",
    "counter_interval": {
        "start_ms": start, "end_ms": end,
        "duration_ms": duration,
        "duration_s": round(duration / 1000, 3),
        "source": "provider-before.json captured_at_ms -> provider-after.json captured_at_ms"},
    "provider_identity": {
        "instance_id": before["provider_instance_id"],
        "unchanged": before["provider_instance_id"] == after["provider_instance_id"],
        "socket_unchanged": before["socket_path"] == after["socket_path"],
        "agent_unchanged": before["agent_id"] == after["agent_id"],
        "boot_matches_helper_anchor": prov_boot.replace("-", "") == boot},
    "helper_subscription": {
        "subscription_id": pubs[0]["_SYSTEMD_INVOCATION_ID"],
        "same_subscription_all_pubs": same_subscription,
        "same_unit": same_unit,
        "same_helper_pid": same_helper_pid,
        "same_boot": same_boot,
        "same_target": same_target},
    "publications": {
        "count": len(pubs),
        "serials": serials,
        "distinct_serials": len(serials) == len(pubs),
        "all_in_counter_interval": all(in_interval),
        "realtime_us": [int(r["__REALTIME_TIMESTAMP"]) for r in pubs]},
    "journal_window_covers_counter_interval": covered,
    "quote_deltas": {
        "workload": wq_delta, "node": node_delta,
        "generation_elapsed_ns_unchanged":
            before["workload"]["generation_elapsed_ns"] == after["workload"]["generation_elapsed_ns"],
        "interpretation": "zero new Workload Quote calls inside the counter interval"},
    "business_side_separate": {
        "planned": 180, "actual": 180, "success": 155, "unknown": 25,
        "unknown_split": {"in_window": 19, "outside_window": 6},
        "rejected": 0, "timeout": 0, "overload": 0,
        "continuity_coverage": "UNKNOWN", "verdict": "FAIL",
        "ip1_corrections": {
            "coverage_insufficiency_ms": 120768,
            "client_side_serials_over_900s": 8,
            "note": "client measurement ended 1791532367347 before completed_at_ms+20000=1791532488115"}},
    "third_publication_client_coverage": {
        "third_pub_realtime_us": int(pubs[2]["__REALTIME_TIMESTAMP"]),
        "client_measurement_frozen_ms": client_end_ms,
        "third_pub_after_client_freeze": third_pub_ms > client_end_ms,
        "claim": "no complete end-to-end client coverage claimed for the third publication; the S1 identity-reuse conclusion is server-side (Helper journal + Provider counters) only"},
    "source_sha256": {
        "provider-before.json": sha(SRV / "provider-before.json"),
        "provider-after.json": sha(SRV / "provider-after.json"),
        "helper-journal-window.json": sha(SRV / "helper-journal-window.json"),
        "helper-journal-anchor.json": sha(SRV / "helper-journal-anchor.json"),
        "workload-before.json": sha(SRV / "workload-before.json"),
        "workload-after.json": sha(SRV / "workload-after.json")},
    "recorded_sha256_observation_json": {
        "provider-before": "da8abcb47f7ab9389a0c49bb87cfa6520f2030487b6c3480074670b0d2fa6896",
        "provider-after": "01c173fd776a52a6398f92295acbd66d6db2206abca511cd9902d50dd5b57c33",
        "helper-journal-window": "176ac776101eae2e6e9c75b823426da01bfa7eba8347ad5ff488212807a77b5d",
        "helper-journal-anchor": "4abe20619311f44147843d8c9de807c4b8473207f5b65c91bc02fa8ca3ff69ba"},
    "citable_conclusion": {
        "zh": f"{round(duration/1000, 3)} 秒内三次 Workload SVID 发布（三个不同 serial），Workload Quote 增量 0/0/0",
        "en": f"three Workload SVID publications (three distinct serials) within {round(duration/1000, 3)} s; Workload Quote deltas 0/0/0",
        "scope": "server-side identity reuse only; separate from business continuity; third publication has no complete client coverage"},
}

checks = {
    "duration == 600281 ms": duration == 600281,
    "same provider instance": before["provider_instance_id"] == after["provider_instance_id"],
    "three distinct serials": len(serials) == 3 and len(serials) == len(pubs),
    "all pubs in interval": all(in_interval),
    "journal window covers interval": covered,
    "workload deltas 0/0/0": wq_delta == {"attempted": 0, "generated": 0, "failed": 0},
    "node deltas 0/0/0": node_delta == {"attempted": 0, "generated": 0, "failed": 0},
    "same helper subscription": same_subscription and same_unit and same_helper_pid,
    "same target": same_target,
    "third pub after client freeze": result["third_publication_client_coverage"]["third_pub_after_client_freeze"],
}
result["checks"] = checks
(OUT / "s1-recheck.json").write_text(json.dumps(result, indent=1) + "\n")
print(json.dumps({**{"citable": result["citable_conclusion"]["zh"]},
                  **{k: v for k, v in checks.items()}}, indent=1))
print("wrote s1-recheck.json")
