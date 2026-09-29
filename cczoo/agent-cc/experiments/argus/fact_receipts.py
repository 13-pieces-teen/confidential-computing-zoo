#!/usr/bin/env python3
"""Assess complete synthetic fact reads against real admission and fault evidence.

This is passive experiment analysis. It neither grants admission nor claims
model consumption, persistence, or zero reads across missing telemetry.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re

from common import atomic, read, require, sha
from timeline import acceptance, missing, relative


def _path(base, value):
    return (base / value).resolve()


def admission_records(context, base):
    records, diagnostics = [], []
    for item in context.get("admissions", []):
        try:
            if "bundle" in item:
                from admission_evidence import verify
                bundle = _path(base, item["bundle"])
                verified = verify(bundle, _path(base, item["verifier_bin"]), _path(base, item["policy_bin"]))
                require(verified.get("result") == "PASS", "admission archive did not verify")
                admission = verified.get("admission", {})
                require(admission.get("status") == "OBSERVED_ADMITTED" and admission.get("run_id") == context["run_id"], "EAR alone or another run is not observed admission")
                require(item.get("sha256") == verified["bundle_manifest_sha256"], "admission manifest checksum differs")
                records.append(dict(admission, nonce=verified.get("nonce"),
                                    source_sha256=verified["bundle_manifest_sha256"], evidence="archive_and_observed_admission"))
            else:
                directory = _path(base, item["observation"])
                path = directory / "observation.json"
                require(sha(path) == item["sha256"], "admission observation checksum differs")
                observed = read(path)
                require(observed.get("schema") == "argus.e1-observation.v1" and observed.get("run_id") == context["run_id"], "wrong admission observation")
                target = observed.get("registered_target", {})
                status = observed.get("status_after", {})
                verification = observed.get("production_verification", {})
                proof = verification.get("svid_and_business", {})
                require(observed.get("actual_admission") == "ADMITTED" and status.get("ready") is True
                        and status == observed.get("status_before") and observed.get("target_check", {}).get("result") == "MATCH"
                        and target and target == read(directory / "target.json") and sha(directory / "target.json") == observed.get("target_sha256")
                        and verification.get("target") == target and proof.get("server_serial") == status.get("target_serial")
                        and proof.get("server_spiffe_id") == observed.get("target_id"), "incomplete actual admission observation")
                records.append({"target": target, "observed_at_ms": observed["completed_at_ms"],
                                "helper_invocation_id": status.get("helper_invocation_id"),
                                "server_serial": proof.get("server_serial"), "nonce": observed.get("accepted_workload_nonce"),
                                "source_sha256": sha(path), "evidence": "production_verify_observation"})
        except (OSError, ValueError, KeyError, TypeError) as error:
            diagnostics.append({"result": "UNKNOWN", "error_class": type(error).__name__})
    return sorted(records, key=lambda r: r["observed_at_ms"]), diagnostics


def _same_instance(row, target):
    process = row.get("process", {})
    return (bool(target.get("container_id") and target.get("launch_id"))
            and row.get("instance_id") == target["container_id"] and row.get("launch_id") == target["launch_id"]
            and all(process.get(k) is not None and target.get(k) is not None
                    and str(process[k]) == str(target[k]) for k in ("pid", "start_time", "boot_id")))


def _instance_key(target):
    fields = ("container_id", "launch_id", "pid", "start_time", "boot_id")
    return tuple(str(target[k]) for k in fields) if all(target.get(k) is not None and str(target[k]) for k in fields) else None


def _recovery_status(admission, admissions, fault, uncertainty):
    """A same-instance observation needs an actual pre-fault comparison."""
    if not fault or admission["observed_at_ms"] - uncertainty <= fault["started_at_ms"]:
        return "NOT_RECOVERED"
    current, failed = _instance_key(admission["target"]), _instance_key(fault["target"])
    if not current or not failed:
        return "UNKNOWN"
    if current != failed:
        # admission_records already requires the complete positive production
        # observation. A different process/launch establishes its own admission.
        return "OBSERVED"
    old = [a for a in admissions if _instance_key(a["target"]) == current
           and a["observed_at_ms"] + uncertainty < fault["started_at_ms"]]
    if not old:
        return "UNKNOWN"
    for key in ("nonce", "helper_invocation_id"):
        if admission.get(key) and all(a.get(key) and admission[key] != a[key] for a in old):
            return "OBSERVED"
    comparable = any(admission.get(key) and a.get(key) for key in ("nonce", "helper_invocation_id") for a in old)
    return "NOT_RECOVERED" if comparable else "UNKNOWN"


def _recovered(admission, admissions, fault, uncertainty):
    """Fresh observed admission is a milestone independently of a later read."""
    return _recovery_status(admission, admissions, fault, uncertainty) == "OBSERVED"


def _stop_scope(fault):
    """A supervision fault does not establish invalid launch history."""
    event = (fault or {}).get("event")
    if event in ("helper-freeze", "helper-crash"):
        return "supervision_stop"
    if event in ("target-exit", "config-change", "same-container-restart"):
        return "instance_condition_stop"
    return "stop_condition_unestablished" if fault else "no_fault"


def _clock(row, origin, uncertainty, *, at="at_ms", mono="monotonic_ns"):
    return relative(row, origin, at=at, mono=mono, uncertainty_ms=uncertainty)


def _coverage(rows, start, end, origin, uncertainty):
    sources = {r.get("source_id"): r for r in rows if r.get("type") == "source_seen"}
    result = []
    for sid, source in sources.items():
        complete, unknown, exit_at = [], [], None
        # Source registration time cannot hide an earlier unobserved read. The
        # initial target needs coverage from the requested window's start.
        initial = next((r.get("binding", {}).get("instance_id") for r in rows if r.get("type") == "receiver_start"), None)
        began = _clock(source, origin, uncertainty, at="source_started_at_ms", mono="source_started_monotonic_ns")
        left = max(start, began["lower_ms"]) if source.get("instance_id") != initial and began else start
        for row in rows:
            if row.get("source_id") != sid:
                continue
            if row.get("type") == "coverage_interval":
                a = _clock(row, origin, uncertainty, at="started_at_ms", mono="started_monotonic_ns")
                b = _clock(row, origin, uncertainty, at="ended_at_ms", mono="ended_monotonic_ns")
                if a and b:
                    if row.get("status") == "COMPLETE":
                        complete.append([a["upper_ms"], b["lower_ms"]])
                    else:
                        unknown.append([a["lower_ms"], b["upper_ms"]])
            elif row.get("type") == "source_exited" and row.get("boundary") == "original_pid_starttime_absent":
                when = _clock(row, origin, uncertainty)
                if when:
                    complete.append([when["upper_ms"], end])
                    exit_at = when["upper_ms"]
                    # Future reads by this exact PID/starttime cannot occur.
                    unknown = [[a, min(b, when["upper_ms"])] for a, b in unknown]
        if exit_at is not None:
            unknown = [[a, min(b, exit_at)] for a, b in unknown if a < exit_at]
        gaps = missing(complete, left, end) + [[max(left, a), min(end, b)] for a, b in unknown if b > left and a < end]
        result.append({"source_id": sid, "instance_id": source.get("instance_id"),
                       "result": "PASS" if source.get("fact_matching") is True and not gaps else "UNKNOWN",
                       "unknown_intervals_ms": gaps, "fact_matching": source.get("fact_matching") is True})
    # Every registered observation target must have an observed source. Merely
    # adding a binding does not prove that its reader was instrumented.
    bindings = [r["binding"] for r in rows if r.get("type") in ("receiver_start", "target_added") and r.get("binding")]
    observed = {r.get("instance_id") for r in sources.values()}
    absent = [b["instance_id"] for b in bindings if b.get("instance_id") not in observed]
    sequences = [r.get("record_seq") for r in rows if r.get("collector_id")]
    intact = bool(sequences) and sequences == list(range(1, len(sequences) + 1)) and not any(r.get("type") == "receiver_gap" for r in rows)
    return {"result": "PASS" if result and not absent and intact and all(s["result"] == "PASS" for s in result) else "UNKNOWN",
            "sources": result, "unobserved_instances": absent, "record_sequence_complete": intact}


def recovery_summary(context, base, fault, admissions, reads, steps, uncertainty, native_result, native_sha256):
    stages = ("recovery_command_started", "storage_ready", "admission_completed", "entry_ready", "first_compliant_read", "first_task_completed")
    milestones = {stage: {"stage": stage, "status": "NOT_RUN", "at_ms": None} for stage in stages}
    if not fault:
        return {"result": "NOT_RUN", "milestones": list(milestones.values())}
    for row in milestones.values():
        row["status"] = "UNKNOWN"
    command = native_result.get("controls", {}).get("recovery", {})
    if type(command.get("started_at_ms")) is int and command["started_at_ms"] - uncertainty > fault["started_at_ms"]:
        milestones["recovery_command_started"].update(status="OBSERVED", at_ms=command["started_at_ms"],
            source_sha256=native_sha256, timing="native_control_invocation", outcome=command.get("status", "UNKNOWN"))
    else:
        return {"result": "UNKNOWN", "milestones": list(milestones.values()),
                "reason": "no observed recovery command; post-fault activity is not recovery timing"}
    anchor = command["started_at_ms"]
    recovered = [a for a in admissions if _recovered(a, admissions, fault, uncertainty)
                 and a["observed_at_ms"] - uncertainty >= anchor]
    fresh_reads = [r for r in reads if r["phase"] == "recovery" and r["result"] == "PASS"
                   and r["at_ms"] - uncertainty >= anchor
                   and any(r.get("admission_source_sha256") == a["source_sha256"] for a in recovered)]
    if recovered:
        first = recovered[0]
        milestones["admission_completed"].update(status="OBSERVED", at_ms=first["observed_at_ms"], source_sha256=first["source_sha256"],
                                                  target=first["target"], timing="conservative_observation_completion")
    if context.get("storage_probe"):
        item = context["storage_probe"]
        path = _path(base, item["path"])
        try:
            value = read(path)
            require(sha(path) == item["sha256"] and value.get("schema") == "argus.storage-ready.v1"
                    and value.get("run_id") == context["run_id"] and value.get("result") == "PASS"
                    and isinstance(value.get("observed_content_sha256"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", value["observed_content_sha256"]) is not None
                    and value.get("observed_content_sha256") == value.get("expected_content_sha256")
                    and any(value.get("target") == a["target"] for a in recovered)
                    and value["started_at_ms"] - uncertainty >= anchor
                    and value["completed_at_ms"] >= value["started_at_ms"], "storage observation differs")
            milestones["storage_ready"].update(status="OBSERVED", at_ms=value["completed_at_ms"], source_sha256=sha(path),
                                               target=value["target"], timing="completed_read_probe")
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if context.get("lifecycle_file"):
        path = _path(base, context["lifecycle_file"])
        events = acceptance.probe_rows(path, context["run_id"])
        for row in events:
            if (row.get("run_id") == context["run_id"] and row.get("type") == "entry_ready" and row.get("verified") is True
                    and row.get("source") == "read_only_readiness_and_systemctl_poll" and row.get("at_ms", 0) - uncertainty >= anchor
                    and any(row.get("target") == a["target"] and row.get("helper_invocation_id") == a.get("helper_invocation_id") for a in recovered)):
                milestones["entry_ready"].update(status="OBSERVED", at_ms=row["at_ms"], source_sha256=sha(path), timing="poll_observation")
                break
    if fresh_reads:
        first = min(fresh_reads, key=lambda r: r["at_ms"])
        milestones["first_compliant_read"].update(status="OBSERVED", at_ms=first["at_ms"], request_id=first["request_id"],
                                                   instance_id=first["instance_id"], fact_id=first["fact_id"], timing="asgi_application_read")
        completed = [s for s in steps.values() if s.get("task_result") == "PASS" and type(s.get("completed_at_ms")) is int
                     and s["completed_at_ms"] - uncertainty >= anchor
                     and any(r["fact_id"] == s.get("fact_id") and s["completed_at_ms"] - uncertainty >= r["at_ms"] for r in fresh_reads)]
        if completed:
            first = min(completed, key=lambda s: s["completed_at_ms"])
            milestones["first_task_completed"].update(status="OBSERVED", at_ms=first["completed_at_ms"], client_id=first["client_id"],
                                                       task_id=first["step_id"], timing="independent_task_score")
    previous = None
    for milestone in milestones.values():
        stamp = milestone["at_ms"]
        milestone["elapsed_from_recovery_command_ms"] = None if stamp is None else {
            "lower_ms": max(0, stamp - anchor - uncertainty), "upper_ms": stamp - anchor + uncertainty}
        milestone["elapsed_from_previous_stage_ms"] = None
        if stamp is not None and previous is not None:
            milestone["elapsed_from_previous_stage_ms"] = {
                "lower_ms": stamp - previous - 2 * uncertainty, "upper_ms": stamp - previous + 2 * uncertainty}
        previous = stamp
    milestones["recovery_command_started"]["elapsed_from_recovery_command_ms"] = {"lower_ms": 0, "upper_ms": 0}
    return {"result": "OBSERVED" if all(m["status"] == "OBSERVED" for m in milestones.values()) else "UNKNOWN",
            "milestones": list(milestones.values()), "clock_uncertainty_ms": uncertainty,
            "anchor_at_ms": anchor,
            "scope": "observed milestones after explicit recovery command; between-stage intervals compare observations, not causal stage durations"}


def assess(manifest_path, result_path, receiver_path, context_path):
    manifest, result, context = read(manifest_path), read(result_path), read(context_path)
    base = Path(context_path).resolve().parent
    run = context["run_id"]
    require(context.get("schema") == "argus.fact-receipt-context.v1" and result.get("run_id") == run, "fact evidence run differs")
    if type(result.get("started_at_ms")) is not int:
        return {"schema": "argus.fact-receipts.v1", "run_id": run, "result": "NOT_RUN",
                "facts": [{"fact_id": f["fact_id"], "client_id": f["client_id"], "task_id": f["step_id"], "step_id": f["step_id"],
                           "result": "NOT_RUN", "received": "UNKNOWN", "attempted_delivery": False, "interception_success": False, "reads": []}
                          for f in manifest["facts"]], "coverage": {"result": "NOT_RUN"}, "whole_window": None,
                "post_bound": None, "recovery": {"result": "NOT_RUN", "milestones": []}, "reason": "task measurement did not start"}
    uncertainty = context.get("clock_uncertainty_ms", 0)
    require(type(uncertainty) is int and uncertainty >= 0, "nonnegative clock uncertainty required")
    fault = acceptance.fault_checkpoint(_path(base, context["fault_file"])) if context.get("fault_file") else None
    if fault:
        require(fault.get("run_id") == run and fault.get("executed") is True and fault.get("target"), "executed matching fault required")
        require(type(context.get("bound_ms")) is int and context["bound_ms"] > 0, "positive closure bound required")
    origin = fault or {"started_at_ms": result["started_at_ms"]}
    rows = [r for r in acceptance.receiver_rows(receiver_path, run) if r.get("run_id") == run]
    starts = [r for r in rows if r.get("type") == "receiver_start"]
    require(len(starts) == 1 and starts[0].get("schema_version") == 2, "one v2 receiver stream required")
    collector = starts[0]["collector_id"]
    require(all(r.get("collector_id", collector) == collector for r in rows), "mixed collectors")
    bindings = [r["binding"] for r in rows if r.get("type") in ("receiver_start", "target_added") and r.get("binding")]
    if fault:
        require(any(acceptance.same_target(b, fault["target"]) for b in bindings), "fault target differs from receiver binding")
    admissions, errors = admission_records(context, base)
    incomplete_end = False
    if "observation_end_at_ms" in context:
        end_wall, end_source = context["observation_end_at_ms"], "explicit_context_window"
        require(type(end_wall) is int and end_wall >= result["started_at_ms"], "valid observation end required")
    elif type(result.get("completed_at_ms")) is int:
        end_wall, end_source = result["completed_at_ms"], "completed_native_run"
        require(end_wall >= result["started_at_ms"], "native completion precedes run start")
    else:
        # A interrupted native run can still contain definite receiver violations.
        # Retain its known prefix; absence after the last record is not proven.
        end_wall = max([result["started_at_ms"]] + [r[k] for r in rows for k in ("at_ms", "ended_at_ms")
                                                    if type(r.get(k)) is int])
        end_source, incomplete_end = "last_receiver_observation_only", True
    start_rel = result["started_at_ms"] - origin["started_at_ms"] - uncertainty
    end_rel = end_wall - origin["started_at_ms"] + uncertainty
    coverage = _coverage(rows, start_rel, end_rel, origin, uncertainty)
    if incomplete_end:
        coverage.update(result="UNKNOWN", reason="native run has no completion or explicit observation end; unobserved tail remains unknown")
    facts, all_reads = [], []
    steps = {(r["client_id"], r["step_id"]): r for r in result.get("steps", result.get("task_results", []))}
    matched_sources = {r.get("source_id"): r for r in rows if r.get("type") == "source_seen"}
    stop_scope = _stop_scope(fault)

    def policy(row, when):
        matching = [a for a in admissions if _same_instance(row, a["target"]) and a["observed_at_ms"] + uncertainty <= row["at_ms"]]
        if not matching:
            return "UNKNOWN", "no_fault" if not fault else "pause", None, "admission_unestablished", "no matching positive admission observation; absence does not prove rejection"
        latest = matching[-1]
        if _recovered(latest, admissions, fault, uncertainty):
            reason = ("different instance has an independent positive admission observation before this read"
                      if _instance_key(latest["target"]) != _instance_key(fault["target"])
                      else "same-instance admission observation has a changed nonce or Helper invocation against the pre-fault baseline")
            return "PASS", "recovery", latest, "admitted_instance", reason
        if fault and not _same_instance(row, fault["target"]):
            return "PASS", "unaffected", latest, "admitted_instance", "observed admission belongs to a different instance than the fault target"
        if not fault or when["upper_ms"] < context["bound_ms"]:
            return "PASS", "no_fault" if not fault else "before_bound", latest, stop_scope, "observed admission; no exceeded stop threshold"
        if when["lower_ms"] >= context["bound_ms"]:
            if _recovery_status(latest, admissions, fault, uncertainty) == "UNKNOWN":
                return "UNKNOWN", "pause", latest, "readmission_unestablished", "same-instance post-fault observation lacks a comparable pre-fault admission baseline"
            if stop_scope == "stop_condition_unestablished":
                return "UNKNOWN", "pause", latest, stop_scope, "fault record does not identify a supported stop condition"
            return "FAIL", "pause", latest, stop_scope, "affected instance read after declared stop threshold and before fresh admission"
        return "UNKNOWN", "boundary", latest, stop_scope, "clock interval overlaps declared stop threshold"

    for fact in manifest["facts"]:
        step = steps.get((fact["client_id"], fact["step_id"]), {})
        reads = []
        for row in rows:
            if row.get("type") != "fact_observed" or row.get("fact_id") != fact["fact_id"]:
                continue
            source = matched_sources.get(row.get("source_id"))
            if (not source or row.get("provenance") != "kernel_process_and_deployment"
                    or row.get("instance_id") != source.get("instance_id") or row.get("process") != source.get("process")
                    or row.get("full_fact_sha256") != fact["full_fact_sha256"] or row.get("fact_bytes") != fact["fact_bytes"]):
                continue
            when = _clock(row, origin, uncertainty)
            if not when or when["lower_ms"] > end_rel or when["upper_ms"] < start_rel:
                continue
            decision, phase, admission, scope, reason = policy(row, when)
            event = {k: row.get(k) for k in ("request_id", "stream_id", "instance_id", "launch_id", "at_ms", "completion_chunk", "start_offset", "end_offset")}
            event.update(result=decision, phase=phase, scope=scope, reason=reason, relative_time=when,
                         admission_source_sha256=admission.get("source_sha256") if admission else None)
            reads.append(event)
            all_reads.append(event | {"fact_id": fact["fact_id"]})
        attempted = bool(step.get("request_ids") or reads)
        planned = step.get("planned_at_ms", result["started_at_ms"] + round(fact["release_offset_s"] * 1000))
        fact_coverage = _coverage(rows, planned - origin["started_at_ms"] - uncertainty, end_rel, origin, uncertainty)
        if incomplete_end:
            fact_coverage.update(result="UNKNOWN", reason="observation end is incomplete")
        decisions = [r["result"] for r in reads]
        decision = ("FAIL" if "FAIL" in decisions else "NOT_RUN" if not attempted else
                    "UNKNOWN" if "UNKNOWN" in decisions or fact_coverage["result"] != "PASS" or not admissions or errors else "PASS")
        phases = {phase: ("FAIL" if any(r["result"] == "FAIL" for r in reads if r["phase"] == phase) else
                          "UNKNOWN" if fact_coverage["result"] != "PASS" or any(r["result"] == "UNKNOWN" for r in reads if r["phase"] == phase) else
                          "PASS" if any(r["phase"] == phase for r in reads) else "NOT_RUN")
                  for phase in ("no_fault", "before_bound", "pause", "recovery", "boundary", "unaffected")}
        # Absence of a complete fact is compliance evidence, not by itself proof
        # that a gate blocked an attempted sensitive request.
        block_events = []
        for event in step.get("transport_evidence") or []:
            try:
                stamp = round(datetime.fromisoformat(event["checked_at"].replace("Z", "+00:00")).timestamp() * 1000)
            except (KeyError, ValueError, TypeError):
                continue
            in_pause = bool(fault and stamp - uncertainty >= fault["started_at_ms"] + context["bound_ms"]
                            and not any(a["observed_at_ms"] <= stamp + uncertainty and _recovered(a, admissions, fault, uncertainty) for a in admissions))
            if in_pause and event.get("request_id") in step.get("request_ids", []) and event.get("event") == "request_blocked_local":
                block_events.append({"request_id": event["request_id"], "at_ms": stamp, "reason": event.get("reason")})
        facts.append({"fact_id": fact["fact_id"], "client_id": fact["client_id"], "task_id": fact["step_id"],
                      "step_id": fact["step_id"], "result": decision, "received": True if reads else False if fact_coverage["result"] == "PASS" else "UNKNOWN",
                      "attempted_delivery": attempted, "interception_success": decision == "PASS" and attempted and not reads and bool(block_events),
                      "local_block_events": block_events,
                      "phases": phases, "reads": reads, "coverage": fact_coverage["result"]})
    body = [r for r in rows if r.get("type") == "received" and r.get("phase") == "body_read" and r.get("provenance") == "kernel_process_and_deployment"
            and (lambda w: w and w["lower_ms"] <= end_rel and w["upper_ms"] >= start_rel)(_clock(r, origin, uncertainty))]
    def counts(post=False):
        selected = [r for r in all_reads if not post or r["relative_time"]["lower_ms"] >= context.get("bound_ms", float("inf"))]
        body_selected = [r for r in body if not post or (lambda w: w and w["lower_ms"] >= context.get("bound_ms", float("inf")))(_clock(r, origin, uncertainty))]
        partial = [r for r in rows if r.get("type") == "fact_partial" and r.get("provenance") == "kernel_process_and_deployment"
                   and (lambda w: w and start_rel <= w["lower_ms"] <= end_rel and (not post or w["lower_ms"] >= context.get("bound_ms", float("inf"))))(_clock(r, origin, uncertainty))]
        stop_reads = [r for r in selected if r["scope"] in ("supervision_stop", "instance_condition_stop")
                      and r["relative_time"]["upper_ms"] >= 0]
        assessed_body = []
        for row in body_selected:
            when = _clock(row, origin, uncertainty)
            decision, phase, _, scope, _ = policy(row, when)
            assessed_body.append(dict(row, result=decision, phase=phase, scope=scope, relative_time=when))
        stop_body = [r for r in assessed_body if r["result"] == "FAIL"]
        stop_window_body = [r for r in assessed_body if r["scope"] in ("supervision_stop", "instance_condition_stop")
                            and r["relative_time"]["upper_ms"] >= 0]
        def last(values):
            row = max(values, key=lambda r: r["relative_time"]["upper_ms"], default=None)
            return None if row is None else {k: row.get(k) for k in
                ("at_ms", "relative_time", "request_id", "instance_id", "fact_id", "scope", "result")}
        return {"observed_body_bytes": sum(r.get("received_body_bytes", 0) for r in body_selected),
                "complete_fact_reads": len(selected), "unique_facts": len({r["fact_id"] for r in selected}),
                "duplicate_complete_fact_reads": len(selected) - len({r["fact_id"] for r in selected}),
                "partial_frame_candidates": len(partial), "partial_frame_candidate_bytes": sum(r["candidate_frame_bytes"] for r in partial),
                # Compatibility name: these are declared stop-condition violations,
                # never an assertion that a replacement lacks valid launch history.
                "forbidden_complete_fact_reads": sum(r["result"] == "FAIL" for r in selected),
                "post_stop_condition_complete_fact_reads": sum(r["result"] == "FAIL" for r in selected),
                "post_stop_condition_unique_facts": len({r["fact_id"] for r in selected if r["result"] == "FAIL"}),
                "post_stop_condition_body_bytes": sum(r.get("received_body_bytes", 0) for r in stop_body),
                "unestablished_admission_complete_fact_reads": sum(r["scope"] == "admission_unestablished" for r in selected),
                "last_complete_fact_read": last(selected), "last_stop_window_fact_read": last(stop_reads),
                "last_body_read": last(assessed_body), "last_stop_window_body_read": last(stop_window_body)}
    uncertain = [r for r in all_reads if r["scope"] == "admission_unestablished"]
    observed_zero = coverage["result"] == "PASS" and not uncertain and bool(admissions) and not errors
    replacement = {
        "result": "PASS" if observed_zero else "UNKNOWN",
        "complete_fact_reads": 0 if observed_zero else None, "unique_facts": 0 if observed_zero else None,
        "unestablished_admission_candidate_reads": len(uncertain),
        "unestablished_admission_candidate_unique_facts": len({r["fact_id"] for r in uncertain}),
        "reason": ("all observed complete fact reads have positive instance admission association and complete coverage"
                   if observed_zero else "E1 ADMITTED/UNKNOWN and old-binding rejection cannot establish an unadmitted replacement interval"),
        "scope": "complete synthetic fact reads in this covered run; candidates are not proven unadmitted replacements"}
    return {"schema": "argus.fact-receipts.v1", "run_id": run, "facts": facts, "coverage": coverage,
            "observation_window": {"started_at_ms": result["started_at_ms"], "ended_at_ms": end_wall,
                                   "end_source": end_source, "end_established": not incomplete_end},
            "whole_window": counts(), "post_bound": counts(True) if fault else None,
            "stop_condition": {"scope": stop_scope, "event": (fault or {}).get("event"),
                               "bound_ms": context.get("bound_ms"), "threshold_is_experiment_condition": True},
            "unadmitted_replacement": replacement,
            "admission_diagnostics": errors, "admissions": admissions,
            "recovery": recovery_summary(context, base, fault, admissions, all_reads, steps, uncertainty, result, sha(result_path)),
            "source_sha256": {"manifest": sha(manifest_path), "result": sha(result_path), "receiver": sha(receiver_path), "context": sha(context_path)},
            "scope": "complete synthetic fact bytes at ASGI application read; not model use or persisted memory"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "result", "receiver", "context", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    atomic(args.output, assess(args.manifest, args.result, args.receiver, args.context))


if __name__ == "__main__":
    main()
