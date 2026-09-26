#!/usr/bin/env python3
"""E2 metadata timeline, read-only lifecycle observer and reproducible windows.

Polling brackets an observable readiness withdrawal / systemd unit stop. It does
not timestamp Helper's first internal detection or prove the final network read.
Receiver telemetry remains nonblocking and never controls the business service.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core/spire/workload/scripts"))
import remote_acceptance as acceptance


def relative(row, fault, *, at="at_ms", mono="monotonic_ns", uncertainty_ms=0, same_server=False):
    """Return an interval relative to fault-command start, in milliseconds."""
    event_clock = fault.get("clock_id") or ("boot:" + str(fault.get("target", {}).get("boot_id", "")).replace("-", ""))
    row_clock = row.get("clock_id") or ("boot:" + str(row.get("process", {}).get("boot_id", "")).replace("-", ""))
    matching = row_clock != "boot:" and row_clock == event_clock
    legacy_server_clock = same_server and (row_clock == "boot:" or event_clock == "boot:")
    if (matching or legacy_server_clock) and type(row.get(mono)) is int and type(fault.get("started_monotonic_ns")) is int:
        delta = (row[mono] - fault["started_monotonic_ns"]) / 1_000_000
        return {"lower_ms": delta, "upper_ms": delta, "clock": "same_host_monotonic"}
    if type(row.get(at)) is int and type(fault.get("started_at_ms")) is int:
        delta = row[at] - fault["started_at_ms"]
        return {"lower_ms": delta - uncertainty_ms, "upper_ms": delta + uncertainty_ms,
                "clock": "wall_with_uncertainty"}
    return None


def lifecycle_window(rows, kind, fault, uncertainty):
    candidates = []
    for row in rows:
        if row.get("type") != kind or row.get("verified") is not True or not row.get("source"):
            continue
        end = relative(row, fault, uncertainty_ms=uncertainty)
        begin = relative(row, fault, at="observed_after_at_ms", mono="observed_after_monotonic_ns", uncertainty_ms=uncertainty)
        if not end:
            continue
        # Polling supplies two actual observations, not a fabricated point time.
        if row.get("timing") == "poll_interval" and not begin:
            continue
        lower = begin["lower_ms"] if begin else end["lower_ms"]
        if end["upper_ms"] < 0 or lower > end["upper_ms"]:
            continue
        candidates.append({"status": "OBSERVED", "lower_ms": lower, "upper_ms": end["upper_ms"],
                           "boundary": row.get("boundary"), "source": row["source"],
                           "clock": end["clock"], "timing": row.get("timing", "recorded_event")})
    return min(candidates, key=lambda r: r["upper_ms"]) if candidates else {"status": "UNKNOWN"}


def union(intervals):
    result = []
    for left, right in sorted(intervals):
        if right <= left:
            continue
        if result and left <= result[-1][1]:
            result[-1][1] = max(result[-1][1], right)
        else:
            result.append([left, right])
    return result


def missing(intervals, left, right):
    cursor, gaps = left, []
    for begin, end in union(intervals):
        if end <= cursor or begin >= right:
            continue
        if begin > cursor:
            gaps.append([cursor, min(begin, right)])
        cursor = max(cursor, min(end, right))
    if cursor < right:
        gaps.append([cursor, right])
    return gaps


def observed_counts(rows, fault, bound, uncertainty, *, byte_field, same_server=False):
    valid = [(r, relative(r, fault, uncertainty_ms=uncertainty, same_server=same_server)) for r in rows]
    valid = [(r, when) for r, when in valid if when is not None]
    out = {"observed_events": len(valid), "observed_bytes": sum(r.get(byte_field, 0) for r, _ in valid)}
    for name, cutoff in (("post_fault", 0), ("post_bound", bound)):
        definite = [r for r, when in valid if when["lower_ms"] >= cutoff]
        ambiguous = [r for r, when in valid if when["lower_ms"] < cutoff <= when["upper_ms"]]
        out[name] = {"bytes": sum(r.get(byte_field, 0) for r in definite),
                     "requests": len({r.get("request_id") for r in definite}),
                     "ambiguous_bytes": sum(r.get(byte_field, 0) for r in ambiguous)}
    out["last_observed_read"] = max((when for _, when in valid), key=lambda r: r["upper_ms"], default=None)
    return out


def summarize(fault, receiver_rows, probe_rows, lifecycle_rows=(), *, bound_ms, clock_uncertainty_ms,
              observation_end_at_ms=None):
    """Summarize existing evidence; missing observations never become zero-delivery."""
    if bound_ms <= 0 or clock_uncertainty_ms < 0:
        raise ValueError("positive declared bound and nonnegative measured clock uncertainty required")
    run = fault.get("run_id")
    if not run or fault.get("executed") is not True or type(fault.get("started_at_ms")) is not int:
        raise ValueError("a matching executed fault checkpoint is required")
    receiver = [r for r in receiver_rows if r.get("run_id") == run]
    probe = [r for r in probe_rows if r.get("run_id") == run]
    lifecycle = [r for r in lifecycle_rows if r.get("run_id") == run]
    result = {"schema": "argus.lifecycle-timeline.v1", "run_id": run, "bound_ms": bound_ms,
              "clock_uncertainty_ms": clock_uncertainty_ms, "tdx_appraisal": "NOT_ASSESSED",
              "scope": "measured application/client read boundaries, not model use or plaintext revocation",
              "detection": lifecycle_window(lifecycle, "detected", fault, clock_uncertainty_ms),
              "entry_stop": lifecycle_window(lifecycle, "entry_stopped", fault, clock_uncertainty_ms)}
    if all(result[key]["status"] == "OBSERVED" for key in ("detection", "entry_stop")):
        detect, stop = result["detection"], result["entry_stop"]
        result["close_after_detection"] = {
            "status": "OBSERVED" if stop["upper_ms"] >= detect["lower_ms"] else "UNKNOWN",
            "lower_ms": stop["lower_ms"] - detect["upper_ms"],
            "upper_ms": stop["upper_ms"] - detect["lower_ms"],
            "note": "negative lower bounds mean polling/clock intervals overlap"}
    else:
        result["close_after_detection"] = {"status": "UNKNOWN"}
    origins = [r for r in receiver if r.get("type") == "receiver_start"]
    origin = origins[0] if len(origins) == 1 else {}
    bound = origin.get("schema_version") == 2 and acceptance.same_target(origin.get("binding"), fault.get("target"))
    collector = origin.get("collector_id")
    bindings = [origin.get("binding", {})] + [r.get("binding", {}) for r in receiver if r.get("type") == "target_added" and r.get("collector_id") == collector]
    allowed = {(b.get("instance_id"), b.get("launch_id")) for b in bindings}
    sources = {r["source_id"]: r for r in receiver if bound and r.get("type") == "source_seen"
               and r.get("source_id") and r.get("process") and r.get("collector_id") == collector
               and (r.get("instance_id"), r.get("launch_id")) in allowed}
    known_ids = {r.get("request_id") for r in probe if r.get("type") in ("request", "stream_start")}
    reads = []
    for row in receiver:
        source = sources.get(row.get("source_id"), {})
        if (source and row.get("type") == "received" and row.get("collector_id") == collector
                and row.get("phase") == "body_read" and row.get("message_type") == "http.request"
                and row.get("provenance") == "kernel_process_and_deployment" and row.get("request_id") in known_ids
                and all(row.get(k) == source.get(k) for k in ("instance_id", "launch_id", "process"))
                and type(row.get("received_body_bytes")) is int and row["received_body_bytes"] > 0):
            reads.append(row)
    # Receiver and fault are collected on the same service host by contract.
    application = observed_counts(reads, fault, bound_ms, clock_uncertainty_ms,
                                  byte_field="received_body_bytes", same_server=True)
    samples = [r for r in probe if r.get("type") == "request"]
    end_wall = observation_end_at_ms if observation_end_at_ms is not None else max(
        (r.get("completed_at_ms", fault["started_at_ms"]) for r in samples), default=fault["started_at_ms"])
    end = end_wall - fault["started_at_ms"] + clock_uncertainty_ms
    coverage = []
    for sid, source in sources.items():
        complete, unknown = [], []
        for row in receiver:
            if row.get("type") != "coverage_interval" or row.get("source_id") != sid or row.get("collector_id") != collector:
                continue
            begin = relative(row, fault, at="started_at_ms", mono="started_monotonic_ns", uncertainty_ms=clock_uncertainty_ms, same_server=True)
            finish = relative(row, fault, at="ended_at_ms", mono="ended_monotonic_ns", uncertainty_ms=clock_uncertainty_ms, same_server=True)
            if begin and finish and begin["upper_ms"] <= finish["lower_ms"]:
                if row.get("status") == "COMPLETE":
                    complete.append([begin["upper_ms"], finish["lower_ms"]])
                else:
                    unknown.append([begin["lower_ms"], finish["upper_ms"]])
        # Unknown intervals dominate complete intervals; preserve crash tails.
        gaps = missing(complete, 0, max(0, end)) + [[max(0, a), min(end, b)] for a, b in unknown if b > 0 and a < end]
        coverage.append({"source_id": sid, "instance_id": source.get("instance_id"),
                         "status": "COMPLETE" if end > 0 and not gaps else "UNKNOWN",
                         "observed_to_ms": max((b for _, b in complete), default=None),
                         "unknown_intervals_ms": union(gaps)})
    try:
        assessment = acceptance.assess(probe, fault, receiver, bound_ms=bound_ms,
                                       clock_uncertainty_ms=clock_uncertainty_ms)
    except (KeyError, TypeError, ValueError):
        assessment = {"result": "UNKNOWN", "receiver_delivery": "UNKNOWN", "reason": "incomplete or invalid input evidence"}
    application.update(post_bound_verdict=assessment.get("receiver_delivery", "UNKNOWN"),
                       coverage=coverage, observation_end_ms=end,
                       absence_claim="limited to complete source coverage; last sample does not prove future closure")
    starts = [r for r in probe if r.get("type") == "probe_start"]
    expected_server = starts[0].get("server_id") if len(starts) == 1 else None
    chunks = [r for r in probe if r.get("type") == "response_chunk" and r.get("boundary") == "client_http_body_read"
              and r.get("peer_verified") is True and r.get("server_id") == expected_server and expected_server
              and r.get("request_id") and type(r.get("response_bytes")) is int and r["response_bytes"] > 0]
    client = observed_counts(chunks, fault, bound_ms, clock_uncertainty_ms, byte_field="response_bytes")
    business_chunks = [r for r in chunks if (type(r.get("http_status")) is int and 200 <= r["http_status"] < 300)
                       or r.get("synthetic_marker_seen") is True]
    client["business_responses"] = observed_counts(business_chunks, fault, bound_ms, clock_uncertainty_ms, byte_field="response_bytes")
    client.update(acceptance.response_observations(probe, fault, bound_ms=bound_ms, uncertainty_ms=clock_uncertainty_ms))
    result.update(application_reads=application, client_reads=client,
                  assessment={k: assessment.get(k) for k in ("result", "traffic", "receiver_delivery", "inflight_delivery", "reason")},
                  fault_command_completion=relative(fault, fault, at="completed_at_ms", mono="completed_monotonic_ns", same_server=True),
                  notes=["Times are relative to fault-command invocation; completion brackets injection execution.",
                         "Readiness withdrawal is a detection proxy; systemd stop is separate from last application read.",
                         "Positive post-bound observations survive incomplete coverage; zero observed bytes is not zero actual bytes."])
    # This is evidence analysis, not a claim that a remote experiment was run locally.
    result["evidence_status"] = "ANALYZED"
    return result


def snapshot(deployment):
    names = [deployment.unit("helper"), deployment.unit("nginx")]
    process = subprocess.run(["systemctl", "show", *names, "--property=Id,LoadState,ActiveState,SubState,MainPID,InvocationID"],
                             check=True, capture_output=True, text=True, timeout=2)
    units = {}
    for block in process.stdout.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if fields.get("Id"):
            units[fields["Id"]] = fields
    if any(name not in units or units[name].get("LoadState") != "loaded" for name in names):
        raise ValueError("configured observer unit is not loaded")
    helper, entry = (units[name] for name in names)
    try:
        receipt = json.loads((deployment.credentials / "ready").read_text())
        target = json.loads(deployment.target.read_text())
        expiry = datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00"))
        ready = (receipt.get("schema_version") == 1 and receipt.get("invocation_id") == helper.get("InvocationID")
                 and receipt.get("target") == target and expiry.tzinfo is not None and expiry > datetime.now(timezone.utc))
        readiness = "valid" if ready else "invalid"
    except FileNotFoundError:
        ready, readiness = False, "absent"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("readiness could not be observed") from None
    return {"ready": ready, "readiness": readiness,
            "helper_active": helper.get("ActiveState") == "active", "helper_invocation": helper.get("InvocationID"),
            "entry_active": entry.get("ActiveState") == "active" and entry.get("MainPID", "0") != "0",
            "entry_stopped": entry.get("ActiveState") in ("inactive", "failed") and entry.get("MainPID") == "0",
            "entry_state": entry.get("ActiveState"), "entry_pid": entry.get("MainPID")}


def observe(deployment, output, run_id, *, duration=60, interval=.25, stop_file=None, sample=snapshot):
    """One finite read-only process; optional stop-file ends it without signals."""
    if not 1 <= duration <= 3600 or not .05 <= interval <= 5:
        raise ValueError("observer duration [1,3600] and interval [.05,5] required")
    if stop_file and Path(stop_file).exists():
        raise ValueError("observer stop-file already exists; use a fresh experiment path")
    deadline = time.monotonic() + duration
    healthy, last_ready, last_entry, seen, failures = False, None, None, set(), 0
    with Path(output).open("x", encoding="utf-8") as stream:
        def emit(row):
            stream.write(json.dumps({"run_id": run_id, "schema": "argus.lifecycle-observation.v1", **row}) + "\n")
            stream.flush()
        emit({"type": "observer_start", **acceptance.clocks(), "interval_ms": interval * 1000})
        while time.monotonic() < deadline and not (stop_file and Path(stop_file).exists()):
            started = acceptance.clocks()
            try:
                state = sample(deployment)
                ended = acceptance.clocks()
                emit({"type": "lifecycle_sample", **ended, "sample_started_at_ms": started["at_ms"], **state})
                if state["ready"]:
                    last_ready = started
                if state["entry_active"]:
                    last_entry = started
                if not healthy and state["ready"] and state["entry_active"] and state["helper_active"]:
                    healthy = True
                    emit({"type": "observer_ready", **ended})
                for kind, changed, prior, boundary in (
                    ("detected", not state["ready"], last_ready, "readiness_withdrawal_or_expiry"),
                    ("entry_stopped", state["entry_stopped"], last_entry, "systemd_unit_inactive_no_main_pid")):
                    if healthy and changed and prior and kind not in seen:
                        emit({"type": kind, **ended, "verified": True, "timing": "poll_interval",
                              "observed_after_at_ms": prior["at_ms"], "observed_after_monotonic_ns": prior["monotonic_ns"],
                              "source": "read_only_readiness_and_systemctl_poll", "boundary": boundary})
                        seen.add(kind)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                failures += 1
                emit({"type": "observer_gap", **acceptance.clocks(), "error": type(error).__name__})
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        emit({"type": "observer_stop", **acceptance.clocks(), "complete": True,
              "healthy_baseline": healthy, "failed_samples": failures})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("analyze")
    for name in ("fault", "receiver", "trace", "output"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--lifecycle")
    p.add_argument("--bound-ms", type=int, required=True)
    p.add_argument("--clock-uncertainty-ms", type=int, required=True)
    p = commands.add_parser("observe")
    for name in ("config", "run-id", "output"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--duration", type=float, default=60)
    p.add_argument("--interval", type=float, default=.25)
    p.add_argument("--stop-file")
    args = parser.parse_args()
    if args.command == "observe":
        from workload import Deployment, protected_file
        deployment = Deployment(json.loads(protected_file(args.config).read_text()))
        observe(deployment, args.output, args.run_id, duration=args.duration, interval=args.interval, stop_file=args.stop_file)
    else:
        fault = acceptance.fault_checkpoint(args.fault)
        result = summarize(fault, acceptance.receiver_rows(args.receiver, fault["run_id"]), acceptance.probe_rows(args.trace, fault["run_id"]),
                           acceptance.json_rows(args.lifecycle) if args.lifecycle else (),
                           bound_ms=args.bound_ms, clock_uncertainty_ms=args.clock_uncertainty_ms)
        acceptance.save(args.output, result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
