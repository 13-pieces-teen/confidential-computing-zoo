#!/usr/bin/env python3
"""E2 r4 offline recompute (independent of the historical analyzer).

Reads the exported E2 r4 crops under the results branch (read-only) and
recomputes the paper-metric boundaries from raw receiver/lifecycle/fault
events. Times are relative to fault-command invocation unless noted.

Outputs (same directory):
  e2-timeline.csv        one row per boundary event with record references
  e2-recomputed.json     recomputed metrics + comparison against the
                         historical timeline.json values (both kept)
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

E2 = Path("/root/argus-results-ip2-20261009-worktree/cczoo/agent-cc/"
          "documents_ly/paper-results-20261009/ip2/batches/"
          "20261009-repair-and-rerun/preexisting/e2-r4")
OUT = Path(__file__).resolve().parent

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def rows(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

def load(p):
    return json.loads(p.read_text())

# ---- inputs ---------------------------------------------------------------
rules = load(E2 / "CROP-RULES.json")
exp = rows(E2 / "receiver/experiment-window.jsonl")
rec = rows(E2 / "receiver/recovery-window.jsonl")
faults = rows(E2 / "originals/fault.jsonl")
life = rows(E2 / "originals/lifecycle.jsonl")
timeline_old = load(E2 / "originals/timeline.json")
result_old = load(E2 / "originals/result.json")

checks = {}
def check(name, ok, detail=""):
    checks[name] = {"ok": bool(ok), "detail": str(detail)}

# 0. crop integrity (same assertions as verify_e2_r4_windows.py)
check("crop_sha256_experiment",
      sha(E2 / "receiver/experiment-window.jsonl") == rules["experiment"]["crop_sha256"])
check("crop_sha256_recovery",
      sha(E2 / "receiver/recovery-window.jsonl") == rules["recovery"]["crop_sha256"])
exp_seq = [r["record_seq"] for r in exp]
rec_seq = [r["record_seq"] for r in rec]
check("record_seq_contiguous_experiment",
      exp_seq == list(range(exp_seq[0], exp_seq[-1] + 1)))
check("record_seq_contiguous_recovery",
      rec_seq == list(range(rec_seq[0], rec_seq[-1] + 1)))
check("terminal_receiver_stop_incomplete",
      exp[-1]["type"] == "receiver_stop" and exp[-1]["complete"] is False)
check("terminal_UNKNOWN_tail",
      exp[-2]["type"] == "coverage_interval" and exp[-2]["status"] == "UNKNOWN"
      and exp[-2]["reason"] == "uncovered_final_or_crash_tail")

# ---- fault injection base --------------------------------------------------
fault = [f for f in faults if f.get("executed")][0]
T0_NS = fault["started_monotonic_ns"]          # fault-command invocation
T0_MS = fault["started_at_ms"]
T_CMPL_NS = fault["completed_monotonic_ns"]
BOUND_NS = 10_000_000_000                      # 10 s stop bound
BOUND_MS = 10_000

def rel_ms(ns):
    return (ns - T0_NS) / 1e6

def rel_wall_ms(ms):
    return ms - T0_MS

# ---- lifecycle markers ------------------------------------------------------
det = next(r for r in life if r["type"] == "detected")
sto = next(r for r in life if r["type"] == "entry_stopped")
obs_stop = next(r for r in life if r["type"] == "observer_stop")
samples = [r for r in life if r["type"] == "lifecycle_sample"]

entry_stop_interval = {
    "lower_ms": rel_ms(sto["observed_after_monotonic_ns"]),
    "upper_ms": rel_ms(sto["monotonic_ns"]),
    "source": "lifecycle entry_stopped poll bracket (observed_after -> poll)",
}
detection_interval = {
    "lower_ms": rel_ms(det["observed_after_monotonic_ns"]),
    "upper_ms": rel_ms(det["monotonic_ns"]),
    "source": "lifecycle detected poll bracket",
}
close_after_detection = {
    "lower_ms": rel_ms(sto["observed_after_monotonic_ns"]) - rel_ms(det["monotonic_ns"]),
    "upper_ms": rel_ms(sto["monotonic_ns"]) - rel_ms(det["observed_after_monotonic_ns"]),
}
fault_command_completion = {"ms": rel_ms(T_CMPL_NS)}

# independent sample-level reconstruction of the same brackets
samples_sorted = sorted(samples, key=lambda s: s["monotonic_ns"])
last_ready = [s for s in samples_sorted if s.get("ready") is True][-1]
first_not_ready = [s for s in samples_sorted if s.get("ready") is not True]
entry_stop_from_samples = {
    "lower_ms": rel_ms(last_ready["monotonic_ns"]),
    "upper_ms": (rel_ms(first_not_ready[0]["monotonic_ns"])
                 if first_not_ready else None),
    "source": "reconstructed from lifecycle_sample ready flags",
}

# ---- application reads from receiver window ---------------------------------
body_reads = [r for r in exp if r["type"] == "received" and r.get("phase") == "body_read"]
body_reads.sort(key=lambda r: r["monotonic_ns"])
http_body_reads = [r for r in body_reads if r.get("message_type") == "http.request"]
disconnects = [r for r in body_reads if r.get("message_type") == "http.disconnect"]
req_ends = {r["request_id"] for r in exp if r["type"] == "request_end"}

last_overall = http_body_reads[-1]
complete_lifecycle_reads = [r for r in http_body_reads if r["request_id"] in req_ends]
last_complete = complete_lifecycle_reads[-1] if complete_lifecycle_reads else None

def in_window(r, lo_ns, hi_ns):
    return lo_ns < r["monotonic_ns"] <= hi_ns

post_fault_events = [r for r in http_body_reads
                     if in_window(r, T_CMPL_NS, T_CMPL_NS + BOUND_NS)]
post_bound_events = [r for r in http_body_reads
                     if r["monotonic_ns"] > T_CMPL_NS + BOUND_NS]
pre_fault_events = [r for r in http_body_reads if r["monotonic_ns"] <= T_CMPL_NS]

app_reads = {
    "total_body_read_events": len(http_body_reads),
    "distinct_read_requests": len({r["request_id"] for r in http_body_reads}),
    "disconnect_events": len(disconnects),
    "last_body_read_overall": {
        "request_id": last_overall["request_id"],
        "record_seq": last_overall["record_seq"],
        "relative_ms_monotonic": rel_ms(last_overall["monotonic_ns"]),
        "relative_ms_wall": rel_wall_ms(last_overall["at_ms"]),
        "note": ("request has no request_end row in this crop "
                 "(inflight/unestablished admission, result UNKNOWN)"),
    },
    "last_body_read_complete_lifecycle": (
        {"request_id": last_complete["request_id"],
         "record_seq": last_complete["record_seq"],
         "relative_ms_monotonic": rel_ms(last_complete["monotonic_ns"]),
         "relative_ms_wall": rel_wall_ms(last_complete["at_ms"])}
        if last_complete else None),
    "post_fault_before_bound": {
        "requests": len({r["request_id"] for r in post_fault_events}),
        "events": len(post_fault_events),
        "bytes": sum(r.get("received_body_bytes") or 0 for r in post_fault_events),
        "window": "fault_completed < t <= fault_completed+10000ms",
    },
    "post_bound": {
        "requests": len({r["request_id"] for r in post_bound_events}),
        "events": len(post_bound_events),
        "bytes": sum(r.get("received_body_bytes") or 0 for r in post_bound_events),
        "window": "t > fault_completed+10000ms",
    },
    "pre_fault": {
        "requests": len({r["request_id"] for r in pre_fault_events}),
        "bytes": sum(r.get("received_body_bytes") or 0 for r in pre_fault_events),
    },
    "total_bytes_all_body_reads": sum(r.get("received_body_bytes") or 0
                                      for r in http_body_reads),
}

# ---- coverage intervals -----------------------------------------------------
covs = [r for r in exp if r["type"] == "coverage_interval"]
complete_covs = [c for c in covs if c["status"] == "COMPLETE"]
unknown_covs = [c for c in covs if c["status"] == "UNKNOWN"]
coverage = {
    "complete_intervals": len(complete_covs),
    "unknown_intervals": len(unknown_covs),
    "complete_span_start_rel_ms": rel_ms(complete_covs[0]["started_monotonic_ns"]),
    "complete_span_end_rel_ms": rel_ms(complete_covs[-1]["ended_monotonic_ns"]),
    "unknown_tail": [
        {"start_rel_ms": rel_ms(c["started_monotonic_ns"]),
         "end_rel_ms": rel_ms(c["ended_monotonic_ns"]),
         "reason": c["reason"]} for c in unknown_covs],
    "gap_between_complete_and_unknown_start_ms": (
        rel_ms(unknown_covs[-1]["started_monotonic_ns"])
        - rel_ms(complete_covs[-1]["ended_monotonic_ns"])),
}
receiver_stop = next(r for r in exp if r["type"] == "receiver_stop")
last_watermark = [r for r in exp if r["type"] == "source_watermark"][-1]

# ---- effective observation end (historical 51410 ms explained) --------------
# timeline.py: end_wall = max(completed_at_ms of client probe request rows);
# observation_end_ms = end_wall - fault_started + clock_uncertainty(20ms).
# So historical 51410 ms => last client probe request completed at wall rel
# 51390 ms. The probe jsonl is NOT part of the exported crops, so this value
# is reproducible only with IP1 client probe originals (listed as missing).
obs_end_candidates = {
    "observer_stop_rel_ms": rel_ms(obs_stop["monotonic_ns"]),
    "receiver_stop_rel_ms": rel_ms(receiver_stop["monotonic_ns"]),
    "last_COMPLETE_coverage_end_rel_ms": coverage["complete_span_end_rel_ms"],
    "last_watermark_rel_ms": rel_ms(last_watermark["monotonic_ns"]),
    "last_body_read_rel_ms_monotonic": rel_ms(last_overall["monotonic_ns"]),
    "historical_observation_end_ms": timeline_old["application_reads"]["observation_end_ms"],
    "explanation": ("historical 51410 ms = last client probe request completion "
                    "(wall rel 51390 ms) + 20 ms clock uncertainty; the "
                    "effective post-bound receiver observation end supported "
                    "by COMPLETE coverage is 53419.37 ms (observed_to_ms)"),
}

# ---- backend health samples -------------------------------------------------
# Historical analyzer semantics (deployment timeline.py backend_availability):
#   start = backend_probe.started_at_ms rel fault wall +-20ms
#   fault_end = fault.completed_at_ms +-20ms
#   after_fault: start_lower > fault_end_upper  => started-20 > completed+20
#   after_entry_stop: start_lower > entry_stop.upper (mono-based, on fault
#   started wall) => started-20 > fault_started + entry_stop_upper
def count_samples(lo_ns, hi_ns=None):
    if hi_ns is None:
        return sum(1 for s in samples_sorted if s["monotonic_ns"] > lo_ns)
    return sum(1 for s in samples_sorted if lo_ns < s["monotonic_ns"] <= hi_ns)

def bp_started(s):
    return s.get("backend_probe", {}).get("started_at_ms", 0)

backend = {
    "total_samples": len(samples),
    "after_fault_raw_monotonic_gt_completed": count_samples(T_CMPL_NS),
    "after_fault_analyzer_semantics": sum(
        1 for s in samples_sorted if bp_started(s) - 20 > fault["completed_at_ms"] + 20),
    "after_entry_stop_analyzer_semantics": sum(
        1 for s in samples_sorted
        if bp_started(s) - 20 > T0_MS + entry_stop_interval["upper_ms"]),
    "after_entry_stop_raw_monotonic_gt_poll": count_samples(sto["monotonic_ns"]),
    "unknown_samples": sum(
        1 for s in samples_sorted
        if not (s.get("backend_probe", {}).get("result") == "OBSERVED"
                and s.get("backend_probe", {}).get("health_ok") is True)),
    "last_sample_rel_ms": rel_ms(samples_sorted[-1]["monotonic_ns"]),
    "first_sample_rel_ms": rel_ms(samples_sorted[0]["monotonic_ns"]),
    "note": ("historical after_fault=180 / after_entry_stop=163 reproduce "
             "exactly with the analyzer's +-20ms wall-uncertainty bands on "
             "backend_probe.started_at_ms; a raw monotonic count gives 181"),
}

# ---- recovery window probes -------------------------------------------------
probe_ids = rules["recovery"]["request_ids"]
recovery_probes = {}
for rid in probe_ids:
    ev = [r for r in rec if r.get("request_id") == rid]
    enters = [r for r in ev if r.get("phase") == "request_enter"]
    ends = [r for r in ev if r["type"] == "request_end"]
    reads = [r for r in ev if r.get("phase") == "body_read"]
    cov_after = [r for r in rec if r["type"] == "coverage_interval"
                 and r["record_seq"] > ends[-1]["record_seq"]]
    recovery_probes[rid] = {
        "request_enter_record": enters[0]["record_seq"],
        "request_end_record": ends[-1]["record_seq"],
        "body_reads": len(reads),
        "body_read_bytes": sum(r.get("received_body_bytes") or 0 for r in reads),
        "next_coverage": cov_after[0]["status"] if cov_after else None,
        "next_coverage_record": cov_after[0]["record_seq"] if cov_after else None,
    }
recovery_watermarks = [r["source_seq"] for r in rec if r["type"] == "source_watermark"]
check("recovery_watermarks_monotonic",
      recovery_watermarks == sorted(recovery_watermarks))
check("recovery_has_COMPLETE_coverage",
      any(r["type"] == "coverage_interval" and r["status"] == "COMPLETE" for r in rec))

# ---- comparison with historical values --------------------------------------
def cmp(old, new, label, tol=1e-6):
    same = abs(old - new) <= tol
    return {"historical": old, "recomputed": new, "match": bool(same),
            "delta": round(new - old, 6), "label": label}

comparison = {
    "entry_stop_interval": {
        "historical": [timeline_old["entry_stop"]["lower_ms"],
                       timeline_old["entry_stop"]["upper_ms"]],
        "recomputed": [entry_stop_interval["lower_ms"],
                       entry_stop_interval["upper_ms"]],
        "match": (abs(timeline_old["entry_stop"]["lower_ms"]
                      - entry_stop_interval["lower_ms"]) < 1e-6
                  and abs(timeline_old["entry_stop"]["upper_ms"]
                          - entry_stop_interval["upper_ms"]) < 1e-6),
    },
    "detection_interval": {
        "historical": [timeline_old["detection"]["lower_ms"],
                       timeline_old["detection"]["upper_ms"]],
        "recomputed": [detection_interval["lower_ms"],
                       detection_interval["upper_ms"]],
    },
    "last_application_read_ms": {
        "historical": timeline_old["application_reads"]["last_observed_read"]["lower_ms"],
        "recomputed": {
            "overall_last_body_read": rel_ms(last_overall["monotonic_ns"]),
            "complete_lifecycle_last_body_read": (
                rel_ms(last_complete["monotonic_ns"]) if last_complete else None),
        },
        "note": ("4612.61ms = last http.request body_read (recomputed exactly; "
                 "historical filter requires probe-known request ids, bytes>0, "
                 "kernel provenance -- the crop contains only such rows). "
                 "4837.67ms in result.json whole_window.last_body_read is the "
                 "http.disconnect (0 bytes, more_body=false) of the inflight "
                 "request 56561dc1 -- a different event scope, also reproduced."),
    },
    "post_fault_before_bound": {
        "historical": timeline_old["application_reads"]["post_fault"],
        "recomputed": app_reads["post_fault_before_bound"],
    },
    "post_bound": {
        "historical": timeline_old["application_reads"]["post_bound"],
        "recomputed": app_reads["post_bound"],
    },
    "backend_availability": {
        "historical": {"after_fault": timeline_old["backend_availability"]["after_fault_samples"],
                       "after_entry_stop": timeline_old["backend_availability"]["after_entry_stop_samples"]},
        "recomputed": {
            "after_fault_analyzer_semantics": backend["after_fault_analyzer_semantics"],
            "after_entry_stop_analyzer_semantics": backend["after_entry_stop_analyzer_semantics"],
            "after_fault_raw_monotonic": backend["after_fault_raw_monotonic_gt_completed"],
        },
    },
    "close_after_detection": {
        "historical": [timeline_old["close_after_detection"]["lower_ms"],
                       timeline_old["close_after_detection"]["upper_ms"]],
        "recomputed": [close_after_detection["lower_ms"],
                       close_after_detection["upper_ms"]],
    },
    "fault_command_completion_ms": {
        "historical": timeline_old["fault_command_completion"]["lower_ms"],
        "recomputed": fault_command_completion["ms"],
    },
    "coverage_observed_to_ms": {
        "historical": timeline_old["application_reads"]["coverage"][0]["observed_to_ms"],
        "recomputed": coverage["complete_span_end_rel_ms"],
    },
    "client_reads": {
        "historical": timeline_old["client_reads"]["business_responses"],
        "recomputed": None,
        "note": "trace.jsonl (sha eb5ccb26...) is not part of the public crops; "
                "client-side metrics are historical-only until IP1 client "
                "originals are attached",
    },
}

out = {
    "schema": "argus.paper-final.e2-recompute.v1",
    "run_id": "paper-20261004t145835z",
    "trial_id": "e2-full-helper-freeze-20261008-r4",
    "inputs": {
        "crops": str(E2),
        "experiment_crop_sha256": sha(E2 / "receiver/experiment-window.jsonl"),
        "recovery_crop_sha256": sha(E2 / "receiver/recovery-window.jsonl"),
        "fault_sha256": sha(E2 / "originals/fault.jsonl"),
        "lifecycle_sha256": sha(E2 / "originals/lifecycle.jsonl"),
        "timeline_sha256": sha(E2 / "originals/timeline.json"),
        "result_sha256": sha(E2 / "originals/result.json"),
    },
    "fault_injection": {
        "event": fault["event"],
        "executed": True,
        "started_at_ms": T0_MS,
        "started_monotonic_ns": T0_NS,
        "completed_monotonic_ns": T_CMPL_NS,
        "command_completion_ms": fault_command_completion["ms"],
        "bound_ms": BOUND_MS,
    },
    "checks": checks,
    "entry_stop_interval": entry_stop_interval,
    "entry_stop_from_samples": entry_stop_from_samples,
    "detection_interval": detection_interval,
    "close_after_detection": close_after_detection,
    "application_reads": app_reads,
    "coverage": coverage,
    "observation_end_candidates": obs_end_candidates,
    "backend_availability": backend,
    "recovery_probes": recovery_probes,
    "comparison": comparison,
    "missing_materials": [
        "trace.jsonl (client HTTP read events) — sha recorded as "
        "eb5ccb264e32beeee5d55a4da23624805d631d62ba09a9a25fb1d3510024605e; "
        "not present in the public crops, so client_reads stays historical-only",
        "client probe jsonl (type=request rows with completed_at_ms) — source "
        "of historical observation_end_ms 51410 (last probe completion wall "
        "rel 51390 ms + 20 ms); needed to re-derive observation_end_ms; "
        "held by IP1 or in the original collection dir"],
    "analyzer_reference": {
        "file": "experiments/argus/timeline.py",
        "repo_head": "12b365ed8d3c73965e69c8fbe3fcb46075fefa6c",
        "sha256": "ef0785f8890d6f909d9f6ea2d042f30f5ffe9186ccdb88728eda70805cd6b091",
        "note": "semantics for application_reads filters, observation_end_ms, "
                "backend_availability bands, coverage clipping reproduced from "
                "this file (read-only; not modified)",
    },
}

(out_path := OUT / "e2-recomputed.json").write_text(json.dumps(out, indent=1) + "\n")

# ---- timeline csv -----------------------------------------------------------
csv_rows = []
def row(event, ref, mono_ns, at_ms, note=""):
    csv_rows.append({
        "event": event, "source_record": ref,
        "monotonic_ns": mono_ns, "at_ms": at_ms,
        "relative_ms_monotonic": round(rel_ms(mono_ns), 3) if mono_ns else "",
        "relative_ms_wall_20ms": f"{round(rel_wall_ms(at_ms),3)}" if at_ms else "",
        "note": note})

row("fault_invocation", f"fault.jsonl executed {fault['type']}", T0_NS, T0_MS)
row("fault_completed", "fault.jsonl completed", T_CMPL_NS, fault["completed_at_ms"])
row("first_body_read(pre-fault)", "experiment-window record 162141",
    last_overall and next(r["monotonic_ns"] for r in http_body_reads if r["record_seq"] == 162141),
    next(r["at_ms"] for r in http_body_reads if r["record_seq"] == 162141),
    "request 56561dc1 chunk 1, 264 bytes, more_body=true")
row("entry_stop_observed_after", "lifecycle entry_stopped",
    sto["observed_after_monotonic_ns"], sto["observed_after_at_ms"],
    "last poll before stop was observed")
row("entry_stop_poll", "lifecycle entry_stopped", sto["monotonic_ns"], sto["at_ms"],
    "poll that observed systemd_unit_inactive_no_main_pid")
row("detection_observed_after", "lifecycle detected",
    det["observed_after_monotonic_ns"], det["observed_after_at_ms"])
row("detection_poll", "lifecycle detected", det["monotonic_ns"], det["at_ms"],
    "readiness_withdrawal_or_expiry")
if last_complete:
    row("last_body_read_complete_lifecycle", f"record {last_complete['record_seq']}",
        last_complete["monotonic_ns"], last_complete["at_ms"], last_complete["request_id"])
row("last_body_read_overall_inflight", f"record {last_overall['record_seq']}",
    last_overall["monotonic_ns"], last_overall["at_ms"],
    f"{last_overall['request_id']} (no request_end in crop)")
row("bound_fault_completed+10000ms", "bound_ms=10000",
    T_CMPL_NS + BOUND_NS, fault["completed_at_ms"] + BOUND_MS, "stop bound")
row("last_COMPLETE_coverage_end", "experiment-window coverage_interval",
    complete_covs[-1]["ended_monotonic_ns"], complete_covs[-1]["ended_at_ms"])
for c in unknown_covs:
    row("UNKNOWN_tail_start", f"coverage record {c['record_seq']}",
        c["started_monotonic_ns"], c["started_at_ms"], "uncovered_final_or_crash_tail")
    row("UNKNOWN_tail_end", f"coverage record {c['record_seq']}",
        c["ended_monotonic_ns"], c["ended_at_ms"], "receiver_stop")
row("receiver_stop", f"record {receiver_stop['record_seq']}",
    receiver_stop["monotonic_ns"], receiver_stop["at_ms"],
    "complete=false coverage=intervals_only")
row("observer_stop", "lifecycle observer_stop", obs_stop["monotonic_ns"],
    obs_stop["at_ms"], "complete=true healthy_baseline=true failed_samples=0")
for rid, p in recovery_probes.items():
    row(f"recovery_probe_{rid}", f"recovery-window records {p['request_enter_record']}-{p['request_end_record']}",
        None, None, f"body_reads={p['body_reads']} bytes={p['body_read_bytes']} "
                    f"next_coverage={p['next_coverage']}@{p['next_coverage_record']}")

with open(OUT / "e2-timeline.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
    w.writeheader()
    w.writerows(csv_rows)

print(json.dumps(comparison, indent=1))
print("wrote", OUT / "e2-recomputed.json", "and", OUT / "e2-timeline.csv")
