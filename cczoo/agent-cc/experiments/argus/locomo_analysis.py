"""Application measurements from bound LoCoMo results, independent of QA quality."""
import csv
import math

from common import digest, require, sha


METRICS = ("locomo_completion_rate", "locomo_valid_completion_rate", "locomo_injection_rate",
           "locomo_deadline_miss_rate", "locomo_task_mean_ms", "locomo_task_p50_ms", "locomo_task_p95_ms",
           "locomo_request_mean_ms", "locomo_request_p95_ms", "locomo_max_in_flight",
           "locomo_recovery_access_ms", "locomo_recovery_task_ms")
OUTCOMES = ("completed", "rejected", "failed", "timeout", "unknown", "deadline_missed", "not_run")
TASK_FIELDS = ("task_id", "planned_index", "source_sample", "category", "status", "outcome", "injection",
               "planned_at_ms", "released_at_ms", "deadline_at_ms", "started_at_ms", "finished_at_ms",
               "deadline_missed", "duration_ms", "invocation_duration_ms", "reason", "provider", "model")
CONTROL_FIELDS = ("planned_at_ms", "started_at_ms", "completed_at_ms", "status", "returncode", "error")


def timing(value):
    require(value is None or type(value) in (int, float) and math.isfinite(value) and value >= 0,
            "invalid LoCoMo timing")
    return value


def count(value):
    require(type(value) is int and value >= 0, "invalid LoCoMo count")
    return value


def latency(value):
    require(isinstance(value, dict), "missing LoCoMo latency summary")
    n = count(value.get("n"))
    result = {key: timing(value.get(key)) for key in ("mean_ms", "p50_ms", "p95_ms")}
    require(all(v is None for v in result.values()) if n == 0 else all(v is not None for v in result.values()),
            "LoCoMo latency count differs")
    return dict(result, n=n)


def application(native, run):
    """The caller has already checked the runner receipt and native-file hash."""
    result = dict.fromkeys(METRICS)
    app = native.get("application")
    if app is None:
        return dict(result, locomo_application_evidence="LEGACY_UNAVAILABLE",
                    comparison_eligible=False, comparison_exclusion_reasons=["legacy application protocol unavailable"]), {}
    planned = count(app.get("planned"))
    require(planned == native["overall"]["tasks"] and planned > 0, "LoCoMo application denominator differs")
    if run.get("planned_tasks") is not None:
        require(planned == run["planned_tasks"], "LoCoMo planned task count differs from manifest")
    outcomes = app.get("outcome_counts", {})
    require(set(outcomes) == set(OUTCOMES) and sum(count(v) for v in outcomes.values()) == planned,
            "LoCoMo application outcomes differ")
    completed, valid, attempted = (count(app.get(k)) for k in ("completed", "valid_completed", "attempted"))
    require(valid <= completed <= planned and attempted <= planned
            and completed == native["overall"]["status_counts"].get("completed", 0), "LoCoMo completion counts differ")
    injections = app.get("injection_counts", {})
    require(sum(count(v) for v in injections.values()) == planned, "LoCoMo injection counts differ")
    deadline = count(app.get("deadline_misses"))
    require(deadline <= planned, "LoCoMo deadline count differs")
    task_latency = latency(app.get("invocation_latency"))
    require(task_latency["n"] <= valid, "LoCoMo latency exceeds valid completions")
    requests = app.get("requests", {})
    request_count = count(requests.get("count"))
    require(sum(count(v) for v in requests.get("outcome_counts", {}).values()) == request_count,
            "LoCoMo request counts differ")
    request_latency = latency(requests.get("latency"))
    require(request_latency["n"] <= requests.get("outcome_counts", {}).get("completed", 0),
            "LoCoMo request latency includes unsuccessful requests")
    execution = native.get("execution", {})
    protocol = {key: execution.get(key) for key in
                ("concurrent_clients", "schedule", "qa_timeout_seconds", "control_times")}
    # Absolute timestamps, command argv and condition do not define a different
    # workload; fault/control arms must retain the same release and control times.
    models = native.get("observed_models") or [["UNKNOWN", "UNKNOWN"]]
    require(isinstance(models, list) and all(isinstance(m, (list, tuple)) and len(m) == 2
            and all(isinstance(x, str) and x for x in m) for m in models), "invalid observed LoCoMo models")
    models = sorted({tuple(m) for m in models})
    reasons = []
    if any("UNKNOWN" in model for model in models):
        reasons.append("observed model unavailable")
    if type(protocol["concurrent_clients"]) is not bool or protocol["qa_timeout_seconds"] is None:
        reasons.append("execution protocol unavailable")
    if execution.get("qa_phase") != "complete":
        reasons.append("measurement window incomplete")
    condition = execution.get("condition", "no_fault")
    require(condition in ("fault", "no_fault"), "invalid LoCoMo condition")
    if run.get("condition") is not None:
        require(condition == run["condition"], "LoCoMo execution condition differs from manifest")
    controls = execution.get("controls", {})
    expected = "completed" if condition == "fault" else "no_fault"
    required_controls = {"fault", "recovery"} if condition == "fault" else set(protocol["control_times"] or {})
    if any(controls.get(name, {}).get("status") != expected or controls.get(name, {}).get("returncode") != 0
           for name in required_controls):
        reasons.append("fault/control execution incomplete")
    if run.get("comparison_eligible") is False:
        reasons.extend(run.get("comparison_exclusion_reasons") or ["runner comparison ineligible"])
    result.update(locomo_application_evidence="OBSERVED", locomo_planned=planned, locomo_attempted=attempted,
        locomo_valid_completed=valid, locomo_injected=injections.get("INJECTION_OBSERVED", 0),
        locomo_completion_rate=completed / planned, locomo_valid_completion_rate=valid / planned,
        locomo_injection_rate=injections.get("INJECTION_OBSERVED", 0) / planned,
        locomo_deadline_miss_rate=deadline / planned, locomo_deadline_misses=deadline,
        locomo_task_mean_ms=task_latency["mean_ms"], locomo_task_p50_ms=task_latency["p50_ms"],
        locomo_task_p95_ms=task_latency["p95_ms"], locomo_task_latency_samples=task_latency["n"],
        locomo_request_count=request_count, locomo_request_coverage=requests.get("coverage", "UNKNOWN"),
        locomo_request_mean_ms=request_latency["mean_ms"], locomo_request_p95_ms=request_latency["p95_ms"],
        locomo_request_latency_samples=request_latency["n"],
        locomo_max_in_flight=count(app.get("concurrency", {}).get("max_in_flight")),
        locomo_recovery_access_ms=timing(app.get("recovery", {}).get("first_access_ms")),
        locomo_recovery_task_ms=timing(app.get("recovery", {}).get("first_task_ms")),
        locomo_observed_models=[list(m) for m in models], locomo_execution_protocol=protocol,
        comparison_eligible=not reasons, comparison_exclusion_reasons=reasons, condition=condition,
        workload_spec=digest([native["source_sha256"], native.get("selection"), native.get("protocol"), protocol, models]))
    result.update({"locomo_outcome_" + key: value for key, value in outcomes.items()})
    result.update({"locomo_request_outcome_" + key: value for key, value in requests.get("outcome_counts", {}).items()})
    return result, {"application": app, "execution": execution, "observed_models": result["locomo_observed_models"],
        "questions": [{key: q.get(key) for key in TASK_FIELDS} for q in native.get("questions", [])],
        "protocol_digest": result["workload_spec"], "comparison_eligible": not reasons,
        "comparison_exclusion_reasons": reasons}


def unobserved(run, *, invalid=False):
    """Retain the declared denominator without inventing task or request outcomes."""
    planned = run.get("planned_tasks")
    require(planned is None or type(planned) is int and planned >= 0, "invalid planned LoCoMo task count")
    status = "UNKNOWN" if invalid or run.get("result") != "NOT_RUN" else "NOT_RUN"
    metrics = dict.fromkeys(METRICS)
    metrics.update(locomo_evidence=status, locomo_application_evidence=status, locomo_planned=planned,
                   pass_value=None, workload_kind="locomo_derived", connection_mode="not_applicable",
                   comparison_eligible=False, comparison_exclusion_reasons=["bound application evidence unavailable"],
                   evidence_scope="application_workload_completion_not_security")
    metrics.update({"locomo_outcome_" + outcome: planned if outcome == status.lower() else 0 if planned is not None else None
                    for outcome in OUTCOMES})
    detail = {"run_id": run["run_id"], "result": status, "planned_tasks": planned, "unobserved_run": True,
              "delivery_compliance": "NOT_ASSESSED_BY_QA", "application": None,
              "outcome_counts": {status.lower(): planned},
              "questions": [dict(planned_index=i + 1, status=status, outcome=status.lower(), injection=status)
                            for i in range(planned or 0)]}
    return metrics, detail


def export(output, results):
    tasks, controls = [], []
    for result in results:
        association = {key: result.get(key) for key in ("run_id", "group", "condition", "block_id", "protocol_digest")}
        tasks.extend(dict(association, **{key: row.get(key) for key in TASK_FIELDS}) for row in result.get("questions", []))
        controls.extend(dict(association, control=name, **{key: row.get(key) for key in CONTROL_FIELDS})
                        for name, row in result.get("execution", {}).get("controls", {}).items())
    for name, rows, fields in (("locomo-task-timeline.csv", tasks, TASK_FIELDS),
                               ("locomo-control-timeline.csv", controls, ("control",) + CONTROL_FIELDS)):
        with (output / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=("run_id", "group", "condition", "block_id", "protocol_digest") + fields)
            writer.writeheader(); writer.writerows(rows)


def render(output, result, plt):
    """Simple per-run timelines; completed invocations are not security verdicts."""
    files = []
    for detail in result.get("locomo_results", []):
        execution = detail.get("execution", {})
        origin = execution.get("qa_started_at_ms")
        rows = detail.get("questions", [])
        if origin is None or not any(q.get("started_at_ms") is not None for q in rows):
            continue
        fig, ax = plt.subplots(figsize=(8, max(3, min(12, 1.5 + .22 * len(rows)))), layout="constrained")
        for i, row in enumerate(rows):
            start, end = row.get("started_at_ms"), row.get("finished_at_ms")
            valid = row.get("status") == "completed" and row.get("injection") == "INJECTION_OBSERVED" and not row.get("deadline_missed")
            color = "#27844b" if valid else "#ba8714"
            if start is not None:
                ax.plot([(start - origin) / 1000, ((end if end is not None else start) - origin) / 1000], [i, i], color=color, marker="|", linewidth=3)
            elif row.get("planned_at_ms") is not None:
                ax.plot((row["planned_at_ms"] - origin) / 1000, i, marker="x", color="#87929d")
        for name, control in execution.get("controls", {}).items():
            if control.get("started_at_ms") is not None:
                ax.axvline((control["started_at_ms"] - origin) / 1000, linestyle="--", linewidth=1,
                           label=name + ": " + str(control.get("status", "UNKNOWN")))
        ax.set(xlabel="Seconds from QA window start", ylabel="Planned question index",
               title=detail["run_id"] + "\nApplication invocation timeline (green: valid completion; amber: other)")
        ax.spines[["top", "right"]].set_visible(False)
        if execution.get("controls"): ax.legend(fontsize=7)
        for extension in ("svg", "pdf"):
            path = output / "figures" / ("locomo-timeline-" + digest(detail["run_id"])[:16] + "." + extension)
            fig.savefig(path); files.append({"path": str(path.relative_to(output)), "sha256": sha(path)})
        plt.close(fig)
    return files
