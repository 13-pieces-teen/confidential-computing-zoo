"""Join independent task and application-read evidence without imputing success."""
import csv
import json
from pathlib import Path

from common import atomic, read, require, sha
from continuous_work_item import SCENARIOS
from continuous_observations import annotate_phases, join_work_items
from continuous_proposal import SCHEMA as PROPOSAL_SCHEMA

OUTCOMES = ("PASS", "FAIL", "UNKNOWN", "NOT_RUN")
SERVICE_FAULTS = ("helper-freeze", "helper-crash", "target-exit")


def fault_scope(run):
    return run.get("fault_scope", "shared_service" if run.get("fault_kind") in SERVICE_FAULTS else "unspecified")


def summarize(steps, receipts=None):
    """All predeclared steps are denominators, including unreleased/queued work."""
    require(steps and len({s["fact_id"] for s in steps}) == len(steps), "missing or duplicate planned facts")
    facts = {f["fact_id"]: f for f in (receipts or {}).get("facts", [])}
    require(len(facts) == len((receipts or {}).get("facts", [])), "duplicate receipt facts")
    cells = {a + "/" + b: 0 for a in ("PASS", "FAIL") for b in ("PASS", "FAIL")}
    axes = {axis: {v: 0 for v in OUTCOMES} for axis in ("task", "receipt")}
    counters = {k: 0 for k in ("offered", "attempted", "received", "committed", "recalled", "deadline_missed", "no_tool_call")}
    unknown = {k: 0 for k in ("received", "committed", "recalled")}
    rows = []
    for step in steps:
        task = step.get("task_result", "NOT_RUN")
        require(task in OUTCOMES, "invalid task outcome")
        fact = facts.get(step["fact_id"], {})
        receipt = fact.get("result", "UNKNOWN")
        require(receipt in OUTCOMES, "invalid receipt outcome")
        reads = fact.get("reads", [])
        received = fact.get("received", bool(reads) if reads else "UNKNOWN")
        # An idle Agent did not exercise the transport guard. Keep its business
        # failure but never count absence of tool calls as successful blocking.
        no_call = step.get("tool_call_count") == 0
        if no_call and receipt == "PASS": receipt = "NOT_RUN"
        row = dict(step, receipt_result=receipt, received=received,
                   control_exercised=not no_call if step.get("tool_call_count") is not None else "UNKNOWN",
                   complete_fact_reads=len(reads))
        rows.append(row)
        axes["task"][task] += 1; axes["receipt"][receipt] += 1
        if task in ("PASS", "FAIL") and receipt in ("PASS", "FAIL"):
            cells[receipt + "/" + task] += 1
        for key in counters:
            value = no_call if key == "no_tool_call" else row.get(key)
            counters[key] += value is True
            if key in unknown: unknown[key] += value not in (True, False)
    n = len(steps)
    detail = {"planned_tasks": n, "counts": counters, "stage_unknown": unknown,
              "axes": axes, "joint_receipt_task": cells, "steps": rows,
              "receipt_scope": "application body reads; no claim about model consumption or stored-data revocation"}
    metrics = {"continuous_tasks": n, "continuous_task_success_rate": axes["task"]["PASS"] / n,
               "continuous_joint_success_rate": cells["PASS/PASS"] / n,
               "continuous_receipt_unknown_rate": axes["receipt"]["UNKNOWN"] / n,
               "continuous_task_unknown_rate": axes["task"]["UNKNOWN"] / n,
               "continuous_deadline_miss_rate": counters["deadline_missed"] / n,
               "continuous_offered_rate": counters["offered"] / n}
    return metrics, detail


def bound_result(output, directory, run):
    path = directory / "continuous/result.json"
    value = read(path)
    require(value.get("schema") == "argus.continuous-result.v1" and value.get("run_id") == run["run_id"]
            and value.get("operation_id"), "continuous result association missing")
    entries = [e for e in read(output / "state.json")["operations"].values()
               if e.get("run_id") == run["run_id"] and e.get("operation_id") == value["operation_id"]
               and e.get("phase") in ("complete", "submission_unknown")]
    require(len(entries) == 1, "continuous result has no unique runner receipt")
    entry = entries[0]
    receipt = (output / entry["evidence"]).resolve()
    require(receipt.is_relative_to(output.resolve()) and sha(receipt) == entry["evidence_sha256"], "runner receipt differs")
    envelope = read(receipt)
    require(envelope.get("schema") == "argus.step.v1" and envelope.get("tool") == "continuous"
            and envelope.get("run_id") == run["run_id"] and envelope.get("operation_id") == value["operation_id"]
            and (receipt.parent / envelope["source"]).resolve() == path.resolve()
            and envelope.get("source_sha256") == sha(path), "continuous native receipt differs")
    manifest_path = path.parent / "manifest.json"
    manifest = read(manifest_path)
    require(manifest.get("run_id") == run["run_id"], "continuous manifest run mismatch")
    require(value.get("manifest_sha256") == sha(manifest_path), "continuous manifest differs from native receipt")
    facts = {f["fact_id"]: f for f in manifest["facts"]}
    steps = value["steps"]
    require(len(facts) == len(steps) and set(facts) == {s["fact_id"] for s in steps}, "planned task denominator changed")
    for step in steps:
        fact = facts[step["fact_id"]]
        require(all(fact.get(k) == step.get(k) for k in ("client_id", "step_id", "full_fact_sha256", "fact_bytes",
                                                       "work_item_id", "constraint_key", "proposal_sha256")),
                "task differs from planned fact")
    if value.get('scenario') == 'work-item-v1':
        require(manifest.get('protocol',{}).get('proposal_schema') == PROPOSAL_SCHEMA
                and all(isinstance(f.get('proposal_sha256'),str) and len(f['proposal_sha256']) == 64 for f in facts.values()),
                'work-item result predates or lacks the typed Proposal contract')
    require(value.get("protocol_digest"), "continuous protocol digest missing")
    require(all(fault_scope(item) in ("unspecified", fault_scope(run)) for item in (value, manifest)),
            "continuous fault domain differs")
    return value


def unobserved(run):
    scenario = run.get('scenario', 'ledger-v1')
    require(scenario in SCENARIOS, 'invalid continuous scenario')
    n = run.get("planned_tasks", 3 * SCENARIOS[scenario] * run["scale"])
    task = "NOT_RUN" if run["result"] == "NOT_RUN" else "UNKNOWN"
    return dict(run_id=run["run_id"], group=run["group"], condition=run.get("condition", "unspecified"),
                block_id=run["block_id"], scale=run["scale"], fault_scope=fault_scope(run),
                fault_kind=run.get("fault_kind", "unspecified"), protocol_digest="unobserved", planned_tasks=n,
                axes={"task": {k: n if k == task else 0 for k in OUTCOMES},
                      "receipt": {k: n if k == "UNKNOWN" else 0 for k in OUTCOMES}},
                joint_receipt_task={a+"/"+b: 0 for a in ("PASS", "FAIL") for b in ("PASS", "FAIL")},
                steps=[], clients=[], unobserved_run=True,
                reason="No bound task result; planned denominator retained without invented facts or timings")


def condition_evidence(value, run, context_path):
    """Control exit status alone is not evidence that the stated fault happened."""
    condition = run.get("condition")
    try:
        context = read(context_path) if context_path.is_file() else {}
    except (ValueError, OSError, KeyError, TypeError):
        return {"result": "UNKNOWN", "reason": "receipt context unreadable"}
    kind = run.get("fault_kind", "unspecified")
    if value.get("fault_kind") not in (None, kind):
        return {"result": "UNKNOWN", "reason": "native fault kind differs from suite"}
    controls = value.get("controls", {})
    if condition == "no_fault":
        ok = not context.get("fault_file") and all(controls.get(k, {}).get("status") == "no_fault" for k in ("fault", "recovery"))
        return {"result": "OBSERVED" if ok else "UNKNOWN", "reason": "no-fault control schedule"}
    if condition != "fault" or not context.get("fault_file"):
        return {"result": "UNKNOWN", "reason": "fault run needs an actual fault checkpoint"}
    try:
        from timeline import acceptance
        path = (context_path.parent / context["fault_file"]).resolve()
        fault = acceptance.fault_checkpoint(path)
        control = controls.get("fault", {})
        uncertainty = context.get("clock_uncertainty_ms", 0)
        require(type(uncertainty) is int and uncertainty >= 0, "invalid clock uncertainty")
        require(fault.get("run_id") == run["run_id"] and fault.get("executed") is True and fault.get("event") == kind,
                "actual fault differs from the declared experiment")
        require(control.get("status") == "completed" and control.get("returncode") == 0
                and control["started_at_ms"] - uncertainty <= fault["started_at_ms"] <= control["completed_at_ms"] + uncertainty,
                "fault checkpoint is outside this control invocation")
        return {"result": "OBSERVED", "event": kind, "source_sha256": sha(path), "fault_started_at_ms":fault['started_at_ms']}
    except (ValueError, OSError, KeyError, TypeError) as error:
        return {"result": "UNKNOWN", "reason": str(error)}


def evidence(output, directory, run):
    value = bound_result(output, directory, run)
    receiver, context = directory / "continuous/receiver.jsonl", directory / "continuous/receipt-context.json"
    receipt = {"schema": "argus.fact-receipts.v1", "result": "UNKNOWN", "facts": [],
               "reason": "receiver/context not collected"}
    condition = condition_evidence(value, run, context)
    try:
        context_value = read(context) if context.is_file() else {}
    except (ValueError,OSError,TypeError):
        context_value = {}
    # A missing checkpoint must not silently turn a fault trial into a no-fault
    # receipt PASS. Task scores remain usable as individual observations.
    if receiver.is_file() and context.is_file():
        try:
            context_value = read(context)
            require(bool(context_value.get("fault_file")) == (run.get("condition") == "fault"),
                    "receipt context does not match fault/no-fault condition")
            from fact_receipts import assess
            receipt = assess(directory / "continuous/manifest.json", directory / "continuous/result.json", receiver, context)
        except (OSError, ValueError, KeyError, TypeError) as error:
            receipt["reason"] = type(error).__name__
    scored_receipt = receipt
    if condition["result"] != "OBSERVED":
        scored_receipt = dict(receipt, facts=[dict(f, result="UNKNOWN") if f.get("result") == "PASS" else f
                                             for f in receipt.get("facts", [])])
    metrics, detail = summarize(value["steps"], scored_receipt)
    detail['steps'] = annotate_phases(detail['steps'],value,condition,context_value.get('clock_uncertainty_ms'))
    if value.get('scenario') == 'work-item-v1':
        work_items = join_work_items(value,scored_receipt,condition)
        require(len(work_items) == run['scale'], 'missing planned work items')
        metrics['continuous_work_item_completion_rate'] = sum(w.get('complete_task_result') == 'PASS' for w in work_items) / len(work_items)
        metrics['continuous_work_item_continuation_rate'] = sum(w.get('continuation_result') == 'PASS' for w in work_items) / len(work_items)
        metrics['continuous_step_success_rate'] = metrics['continuous_task_success_rate']
        metrics['continuous_business_tasks'] = len(work_items)
        metrics['continuous_planned_steps'] = len(value['steps'])
        detail['work_items'] = work_items
        metrics['continuous_legal_recovery_rate'] = (sum(w['legal_recovery_result'] == 'PASS' for w in work_items) / len(work_items)
                                                    if value.get('condition') == 'fault' else None)
        metrics['continuous_legal_recovery_unknown_rate'] = (sum(w['legal_recovery_result'] == 'UNKNOWN' for w in work_items) / len(work_items)
                                                            if value.get('condition') == 'fault' else None)
    exclusion_reasons = []
    if condition["result"] != "OBSERVED": exclusion_reasons.append("condition_evidence_unknown")
    if value.get("model_mismatches"): exclusion_reasons.append("observed_model_mismatch")
    metrics.update(continuous_evidence=value.get("result"), pass_value=None,
                   workload_kind="continuous_agent_tools", connection_mode="not_applicable",
                   workload_spec=value["protocol_digest"], evidence_scope="task_and_application_read_axes_separate",
                   condition_evidence=condition["result"], comparison_eligible=not exclusion_reasons,
                   comparison_exclusion_reasons=exclusion_reasons)
    whole = receipt.get("whole_window") or {}
    for source, target in (("unique_facts", "observed_unique_facts"),
                           ("duplicate_complete_fact_reads", "observed_duplicate_fact_reads"),
                           ("partial_frame_candidate_bytes", "observed_partial_frame_candidate_bytes"),
                           ("observed_body_bytes", "observed_application_body_bytes")):
        if source in whole: metrics[target] = whole[source]
    post = receipt.get("post_bound") or {}
    if "forbidden_complete_fact_reads" in post:
        metrics["observed_post_bound_forbidden_fact_reads"] = post["forbidden_complete_fact_reads"]
    metrics["receipt_stop_scope"] = receipt.get("stop_condition", {}).get("scope", "UNKNOWN")
    for source in ("post_stop_condition_complete_fact_reads", "post_stop_condition_unique_facts", "post_stop_condition_body_bytes"):
        if source in whole: metrics["observed_" + source] = whole[source]
    replacement = receipt.get("unadmitted_replacement", {})
    metrics["unadmitted_replacement_evidence"] = replacement.get("result", "UNKNOWN")
    # Null is deliberate: an unassociated reader is not a proven unadmitted
    # replacement, and no result must not silently become a zero in paper tables.
    metrics["unadmitted_replacement_unique_facts"] = replacement.get("unique_facts")
    metrics["unestablished_admission_candidate_unique_facts"] = replacement.get("unestablished_admission_candidate_unique_facts")
    detail.update(run_id=run["run_id"], group=run["group"], condition=run.get("condition", "unspecified"),
                  fault_kind=run.get("fault_kind", "unspecified"), fault_scope=fault_scope(run), condition_evidence=condition,
                  comparison_eligible=metrics["comparison_eligible"],
                  comparison_exclusion_reasons=exclusion_reasons, model_mismatches=value.get("model_mismatches", []),
                  block_id=run["block_id"], scale=run["scale"], protocol_digest=value["protocol_digest"],
                  source_sha256=sha(directory / "continuous/result.json"), receiver=receipt,
                  started_at_ms=value.get("started_at_ms"), completed_at_ms=value.get("completed_at_ms"),
                  controls=value.get("controls", []), recovery=value.get("recovery", {}))
    detail["clients"] = []
    for client in sorted({s["client_id"] for s in value["steps"]}):
        cm, cd = summarize([s for s in value["steps"] if s["client_id"] == client], scored_receipt)
        if value.get('scenario') == 'work-item-v1':
            item = next(w for w in detail['work_items'] if w['client_id'] == client)
            cm.update(continuous_work_item_completion_rate=int(item['complete_task_result'] == 'PASS'),
                      continuous_work_item_continuation_rate=int(item['continuation_result'] == 'PASS'),
                      continuous_step_success_rate=cm['continuous_task_success_rate'])
        detail["clients"].append(dict(client_id=client, **cm, axes=cd["axes"], counts=cd["counts"]))
    atomic(directory / "continuous/joint-result.json", detail)
    return metrics, detail


def export(output, details):
    """Metadata only: generated facts and Agent answers never enter paper tables."""
    rows = []
    keep = ("client_id", "step_id", "phase", "planned_phase", "actual_dispatch_phase", "actual_tool_phases", "fact_id", "task_result", "receipt_result", "reason",
            "offered", "attempted", "received", "committed", "recalled", "deadline_missed", "tool_call_count",
            "work_item_id", "proposal_sha256", "proposal_persisted", "write_attempted", "write_outcome", "dispatch_attempted", "released_at_ms", "release_source",
            "planned_at_ms", "offered_at_ms", "started_at_ms", "deadline_at_ms", "completed_at_ms", "answer_at_ms", "goal_confirmed_at_ms")
    for run in details:
        for step in run["steps"]:
            rows.append(dict(run_id=run["run_id"], group=run["group"], condition=run["condition"],
                              block_id=run["block_id"], **{k: json.dumps(step[k]) if isinstance(step.get(k),(list,dict)) else step.get(k) for k in keep}))
    with (output / "continuous-tasks.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = ["run_id", "group", "condition", "block_id", *keep]
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    events = []
    for run in details:
        for step in run['steps']:
            for event in step.get('tool_events', []):
                events.append(dict(run_id=run['run_id'],client_id=step['client_id'],step_id=step['step_id'],
                                   planned_phase=step.get('phase'),**event))
    with (output / 'continuous-tool-events.csv').open('w',encoding='utf-8',newline='') as stream:
        fields = ['run_id','client_id','step_id','planned_phase','event','tool_name','tool_call_id','checked_at','at_ms','actual_phase']
        writer = csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore'); writer.writeheader(); writer.writerows(events)
    items = []
    fields = ['run_id','group','condition','block_id','client_id','work_item_id','complete_task_result',
              'continuation_result','legal_recovery_result','legal_recovery_reason','proposals',
              'unapplied_required_proposals','recovery_intervals','legal_recovery_evidence']
    for run in details:
        for item in run.get('work_items', []):
            row = {**{k:run.get(k) for k in ('run_id','group','condition','block_id')},**item}
            items.append({k:json.dumps(row[k],sort_keys=True) if isinstance(row.get(k),(dict,list)) else row.get(k) for k in fields})
    with (output / 'continuous-work-items.csv').open('w',encoding='utf-8',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerows(items)


def fault_contrasts(rows, metrics, mean_ci):
    """Pair fault-control within the SAME arm and structural seed, never requests."""
    result = []
    dimensions = ("case", "scale", "group", "workload_kind", "workload_spec", "client_id", "fault_kind", "fault_scope", "injected_client_id")
    for key in sorted({tuple(str(r.get(k, "all")) for k in dimensions) for r in rows if r.get("condition") in ("fault", "no_fault")}):
        members = [r for r in rows if tuple(str(r.get(k, "all")) for k in dimensions) == key]
        for metric in metrics:
            arms = {"fault": {}, "no_fault": {}}
            for row in members:
                if row.get(metric) is not None and row.get("comparison_eligible", True):
                    arm = arms[row["condition"]]
                    require(row["block_id"] not in arm, "duplicate fault/control block")
                    arm[row["block_id"]] = row[metric]
            paired = sorted(set(arms["fault"]) & set(arms["no_fault"]))
            result.append(dict(zip(dimensions, key), metric=metric,
                               **mean_ci([arms["fault"][k] - arms["no_fault"][k] for k in paired]),
                               direction="fault_minus_no_fault", unit="fraction",
                               paired_blocks=[{"block_id": k, "difference": arms["fault"][k] - arms["no_fault"][k]} for k in paired],
                               n_unpaired_fault=len(set(arms["fault"]) - set(paired)),
                               n_unpaired_control=len(set(arms["no_fault"]) - set(paired))))
    return result


def collateral_losses(rows, mean_ci):
    """Only a predeclared LOCAL injection has an uninjected-client contrast.

    Existing continuous service trials cannot populate this estimator. The
    separate fleet_fault availability diagnostic is not continuous-task input.
    """
    eligible = []
    for row in rows:
        uninjected = row.get("uninjected_client_ids", [])
        injected = row.get("injected_client_id")
        if (row.get("fault_scope") == "local_client" and injected
                and row.get("client_id") in uninjected and injected not in uninjected
                and row.get("fault_assignment_predeclared") is True):
            eligible.append(row)
    contrasts = fault_contrasts(eligible, ["continuous_task_success_rate"], mean_ci)
    for row in contrasts:
        row.update(metric="completion_loss_pp", direction="no_fault_minus_fault", unit="percentage_points")
        for key in ("mean", "low", "high"):
            if row[key] is not None: row[key] *= -100
        row["low"], row["high"] = row["high"], row["low"]
        for block in row["paired_blocks"]: block["difference"] *= -100
    return contrasts
