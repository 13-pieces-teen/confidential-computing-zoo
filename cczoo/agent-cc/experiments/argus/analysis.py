"""Recompute reports from preserved verdicts and request observations.

Bootstrap units are independent runs (or paired blocks), never HTTP requests.
Tail quantiles are descriptive and include their sample counts.
"""
import csv
import html
import math
from pathlib import Path
import random
import statistics

from common import atomic, digest, read, require, sha

STRATA = ("case", "scale", "connection_mode", "workload_kind", "workload_spec")
METRICS = ("pass_value", "api_goodput_rps", "memory_nonempty_goodput_rps", "p95_ms",
           "tcp_connect_p50_ms", "tls_handshake_p50_ms", "connect_p50_ms", "api_p95_ms", "connection_reuse_fraction",
           "process_cpu_seconds", "sum_process_peak_rss_bytes", "locomo_conversation_macro_f1",
           "locomo_answered_f1", "locomo_coverage", "locomo_abstention")


def stratum(row):
    return tuple(row.get(key, "unspecified") for key in STRATA)


def quantile(values, p):
    if not values:
        return None
    v = sorted(values)
    position = (len(v) - 1) * p
    lower = math.floor(position)
    return v[lower] + (v[min(lower + 1, len(v) - 1)] - v[lower]) * (position - lower)


def mean_ci(values, seed=20260926, iterations=2000):
    if not values:
        return {"mean": None, "low": None, "high": None, "n_runs": 0}
    mean = statistics.mean(values)
    if len(values) < 2:
        return {"mean": mean, "low": None, "high": None, "n_runs": len(values)}
    rng = random.Random(seed)
    boot = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(iterations)]
    return {"mean": mean, "low": quantile(boot, .025), "high": quantile(boot, .975), "n_runs": len(values)}


def paired_difference(rows, other, metric):
    full, arm = {}, {}
    for row in rows:
        if row.get(metric) is None:
            continue
        key = (row["block_id"], *stratum(row))
        target = full if row["group"] == "full_argus" else arm if row["group"] == other else None
        if target is not None:
            require(key not in target, "duplicate independent block")
            target[key] = row[metric]
    common = sorted(set(full) & set(arm))
    return dict(mean_ci([full[k] - arm[k] for k in common]), metric=metric, other=other,
                n_unpaired_full=len(set(full) - set(arm)), n_unpaired_other=len(set(arm) - set(full)))


def summarize_requests(rows, measurement_seconds):
    require(type(measurement_seconds) in (int, float) and math.isfinite(measurement_seconds)
            and measurement_seconds > 0, "positive finite measurement duration required")
    measured = [r for r in rows if r.get("phase") == "measurement"]
    dimensions = {}
    for key, allowed in (("connection_mode", ("new", "reuse", "legacy_unspecified")),
                         ("workload_kind", ("status_api", "memory_query", "custom_api", "legacy_unspecified"))):
        values = {r.get(key, "legacy_unspecified") for r in measured}
        require(len(values) <= 1 and values.issubset(allowed), "mixed or invalid " + key + " cannot share statistics")
        dimensions[key] = next(iter(values), "legacy_unspecified")
    require(all(r.get("outcome") in ("success", "rejected", "timeout", "unknown", "overload") for r in measured),
            "unrecognized request outcome")
    require(all(type(r.get("latency_ms")) in (int, float) and math.isfinite(r["latency_ms"])
                and r["latency_ms"] >= 0 for r in measured if r.get("outcome") == "success"),
            "successful requests require finite nonnegative latency")
    counts = {name: sum(r.get("outcome") == name for r in measured)
              for name in ("success", "rejected", "timeout", "unknown", "overload")}
    latencies = [r["latency_ms"] for r in measured if r.get("outcome") == "success" and isinstance(r.get("latency_ms"), (int, float))]
    result = dict(counts, **dimensions, n_requests=len(measured), n_success_latency=len(latencies),
                p50_ms=quantile(latencies, .50), p95_ms=quantile(latencies, .95), p99_ms=quantile(latencies, .99),
                api_goodput_rps=counts["success"] / measurement_seconds if measurement_seconds > 0 else None,
                definition="expected HTTP response completed inside measurement window; not Agent task correctness")
    if dimensions["workload_kind"] == "memory_query":
        successes = [r for r in measured if r.get("outcome") == "success"]
        require(all(r.get("memory_result") in ("nonempty", "empty", "unknown") for r in successes), "missing memory result classification")
        require(all(type(r.get("memory_leaf_count")) is int and r["memory_leaf_count"] > 0
                    for r in successes if r["memory_result"] == "nonempty"), "nonempty memory requires actual leaf count")
        result.update(memory_nonempty_goodput_rps=sum(r["memory_result"] == "nonempty" for r in successes) / measurement_seconds,
                      memory_empty=sum(r["memory_result"] == "empty" for r in successes),
                      memory_unknown=sum(r["memory_result"] == "unknown" for r in successes))
    connection_rows = [dict(connection_attempted=False, connection_created=False, connection_reused=False, reconnect=False, **r)
                       if r.get("outcome") == "overload" and "connection_attempted" not in r else r for r in measured]
    instrumented = [r for r in connection_rows if "connection_attempted" in r]
    if instrumented:
        require(len(instrumented) == len(connection_rows), "partial connection instrumentation")
        for output, field in (("connection_attempts", "connection_attempted"), ("connections_created", "connection_created"),
                              ("requests_reusing_connection", "connection_reused"), ("reconnect_attempts", "reconnect")):
            require(all(type(r.get(field)) is bool for r in connection_rows), "invalid connection observation")
            result[output] = sum(r[field] for r in connection_rows)
        result["connection_reuse_fraction"] = result["requests_reusing_connection"] / len(measured)
        for field, output, percentile in (("tcp_connect_ms", "tcp_connect_p50_ms", .5),
                                          ("tls_handshake_ms", "tls_handshake_p50_ms", .5),
                                          ("connect_ms", "connect_p50_ms", .5), ("api_ms", "api_p95_ms", .95)):
            eligible = [r for r in connection_rows if field == "api_ms" or r["connection_attempted"]]
            values = [r[field] for r in eligible if r.get(field) is not None]
            require(all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values), "invalid connection timing")
            result[output] = quantile(values, percentile)
            result[output + "_samples"] = len(values)
    return result


def locomo_evidence(output, directory, run):
    """Bind native QA scores to the runner receipt, independently of step PASS."""
    path = directory / "locomo/result.json"
    native = read(path)
    require(native.get("schema") == "argus.locomo-result.v1" and native.get("result") in ("COMPLETE", "INCOMPLETE"), "unsupported LoCoMo result")
    require(native.get("run_id") == run["run_id"] and native.get("operation_id"), "LoCoMo runner association missing")
    # A returned INCOMPLETE workload has a saved step receipt, but the runner
    # deliberately leaves its operation resumable/unknown. Its answered and
    # unanswered QA items still belong in workload coverage and quality scores.
    entries = [e for e in read(output / "state.json")["operations"].values()
               if e.get("run_id") == run["run_id"] and e.get("operation_id") == native["operation_id"]
               and (e.get("phase") == "complete" or
                    (e.get("phase") == "submission_unknown" and native["result"] == "INCOMPLETE"))]
    require(len(entries) == 1, "LoCoMo has no unique saved runner receipt")
    entry = entries[0]
    receipt_path = (output / entry["evidence"]).resolve()
    require(receipt_path.is_relative_to(output.resolve()) and sha(receipt_path) == entry["evidence_sha256"], "runner receipt differs")
    receipt = read(receipt_path)
    require(receipt.get("schema") == "argus.step.v1" and receipt.get("tool") == "locomo"
            and receipt.get("run_id") == run["run_id"] and receipt.get("operation_id") == native["operation_id"]
            and receipt.get("evidence_scope") == "application_workload_completion_not_security"
            and receipt.get("native_result") == native["result"], "LoCoMo step binding differs")
    require((receipt_path.parent / receipt["source"]).resolve() == path.resolve()
            and receipt.get("source_sha256") == sha(path), "native QA evidence differs from its step receipt")
    overall = native["overall"]
    tasks = overall["tasks"]
    completed = overall["status_counts"].get("completed", 0)
    require(type(tasks) is int and tasks > 0 and type(completed) is int and 0 <= completed <= tasks, "invalid task coverage")
    require(sum(overall["status_counts"].values()) == tasks and (native["result"] == "COMPLETE") == (completed == tasks), "completion disagrees with task counts")
    result = {"locomo_evidence": native["result"], "locomo_tasks": tasks, "locomo_completed": completed,
              "locomo_coverage": completed / tasks, "locomo_conversation_macro_f1": native.get("conversation_macro_f1"),
              "locomo_answered_f1": overall["token_f1"]["answered_mean"],
              "locomo_abstention": overall["abstention"]["all_tasks_zero_for_uncompleted"],
              "locomo_conversation_clusters": native["cluster_count"], "locomo_source_sha256": native["source_sha256"],
              "workload_spec": digest([native["source_sha256"], native.get("selection")]),
              "evidence_scope": "application_workload_completion_not_security", "pass_value": None,
              "connection_mode": "not_applicable", "workload_kind": "locomo_derived"}
    for metric in ("locomo_conversation_macro_f1", "locomo_answered_f1", "locomo_abstention"):
        value = result[metric]
        require(value is None or (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1), "invalid QA score")
    return result, {"run_id": run["run_id"], "operation_id": native["operation_id"], "result": native["result"],
                    "overall": overall, "by_category": native["by_category"], "by_conversation": native["by_conversation"],
                    "conversation_bootstrap_95": native.get("conversation_bootstrap_95"),
                    "source_sha256": sha(path), "delivery_compliance": "NOT_ASSESSED_BY_QA"}


def summarize_resources(rows, run_id):
    """Sum observed process CPU deltas; report RSS peak sum as an upper bound."""
    groups = {}
    try:
        require(rows and all(r.get("run_id") == run_id and r.get("result") != "UNKNOWN" for r in rows), "resource coverage gap")
        for row in rows:
            require(all(isinstance(row.get(k), (int, float)) and math.isfinite(row[k]) and row[k] >= 0
                        for k in ("pid", "start_ticks", "at_ms", "cpu_seconds", "rss_bytes")), "invalid resource sample")
            groups.setdefault(row["pid"], []).append(row)
        cpu, peaks, duration = 0, 0, []
        for samples in groups.values():
            require(len(samples) >= 2 and len({s["start_ticks"] for s in samples}) == 1, "incomplete or reused PID")
            require(all(b["at_ms"] > a["at_ms"] and b["cpu_seconds"] >= a["cpu_seconds"] for a, b in zip(samples, samples[1:])), "nonmonotonic resource samples")
            cpu += samples[-1]["cpu_seconds"] - samples[0]["cpu_seconds"]
            peaks += max(s["rss_bytes"] for s in samples)
            duration.append((samples[-1]["at_ms"] - samples[0]["at_ms"]) / 1000)
        return {"resources_result": "OBSERVED", "process_cpu_seconds": cpu,
                "sum_process_peak_rss_bytes": peaks, "resource_min_window_seconds": min(duration), "resource_processes": len(groups)}
    except (ValueError, KeyError, TypeError):
        return {"resources_result": "UNKNOWN"}


def analyze(output):
    import json
    output = Path(output)
    verdicts = read(output / "verdicts.json")
    rows, locomo = [], []
    for run in verdicts["runs"]:
        row = dict(run, pass_value=1 if run["result"] == "PASS" else 0 if run["result"] == "FAIL" else None,
                   connection_mode="not_applicable", workload_kind="not_applicable", workload_spec="unspecified")
        directory = output / run.get("evidence_dir", "runs/" + run["run_id"])
        path = directory / "load-result.json"
        if not path.is_file():
            path = directory / "load" / "load-result.json"
        if path.is_file():
            try:
                result = read(path)
                require(result.get("run_id") == run["run_id"], "load evidence belongs to another run")
                for key, allowed in (("connection_mode", ("new", "reuse")), ("workload_kind", ("status_api", "memory_query", "custom_api"))):
                    if result.get(key) in allowed:
                        row[key] = result[key]
                raw = path.parent / "requests.jsonl"
                require(result.get("requests_sha256") == sha(raw), "request trace checksum differs")
                if result.get("result") == "NOT_RUN" and result.get("reason") == "CAPACITY_STOP":
                    row.update(metric_evidence="NOT_RUN", capacity_stop=True,
                               requested_clients=result.get("requested_clients"), provisioned_clients=result.get("provisioned_clients"))
                    if row["result"] != "FAIL":
                        row.update(result="NOT_RUN", pass_value=None)
                    rows.append(row)
                    continue
                require(result.get("measurement_complete") is True, "load measurement was incomplete")
                observations = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
                require(all(r.get("run_id") == run["run_id"] for r in observations), "mixed request runs")
                require(observations and all(isinstance(r.get("request_id"), str) and r["request_id"] for r in observations)
                        and len({r["request_id"] for r in observations}) == len(observations), "missing or duplicate request identity")
                metrics = summarize_requests(observations, result["measurement_seconds"])
                require(all(result.get(key, metrics[key]) == metrics[key] for key in ("connection_mode", "workload_kind")), "declared request stratum differs")
                row.update(metrics)
                require(row["n_requests"] > 0, "no measured requests")
                row["metric_evidence"] = "COMPLETE"
            except (ValueError, OSError, KeyError, TypeError) as error:
                # Corrupt/partial metric artifacts cannot contribute optimistic
                # tails or goodput. Keep the incomplete run visible in the report.
                for metric in METRICS + ("p50_ms", "p99_ms"):
                    row.pop(metric, None)
                row.update(metric_evidence="UNKNOWN", metric_error=type(error).__name__)
                if row["result"] != "FAIL":
                    row.update(result="UNKNOWN", pass_value=None)
        qa = directory / "locomo/result.json"
        if qa.is_file():
            try:
                metrics, detail = locomo_evidence(output, directory, run)
                row.update(metrics)
                locomo.append(detail)
            except (ValueError, OSError, KeyError, TypeError) as error:
                row.update(locomo_evidence="UNKNOWN", locomo_error=type(error).__name__, pass_value=None,
                           connection_mode="not_applicable", workload_kind="locomo_derived",
                           evidence_scope="application_workload_completion_not_security")
                if row["result"] != "FAIL": row["result"] = "UNKNOWN"
        resource = directory / "resources.jsonl"
        if resource.is_file():
            try:
                completion = read(str(resource) + ".complete.json")
                require(completion.get("run_id") == run["run_id"] and completion.get("complete") is True
                        and completion.get("sha256") == sha(resource), "resource measurement was not sealed")
                row.update(summarize_resources([json.loads(line) for line in resource.read_text(encoding="utf-8").splitlines()], run["run_id"]))
            except (ValueError, OSError):
                row["resources_result"] = "UNKNOWN"
        rows.append(row)
    output.mkdir(parents=True, exist_ok=True)
    fields = sorted(set().union(*(r.keys() for r in rows))) if rows else ["run_id"]
    with (output / "statistics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    summaries, comparisons = [], []
    for key in sorted({(*stratum(r), r["group"]) for r in rows}):
        members = [r for r in rows if (*stratum(r), r["group"]) == key]
        summary = dict(zip((*STRATA, "group"), key))
        summary["verdicts"] = {v: sum(r["result"] == v for r in members) for v in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN")}
        summary["locomo_completion"] = {v: sum(r.get("locomo_evidence") == v for r in members) for v in ("COMPLETE", "INCOMPLETE", "UNKNOWN")}
        summary["verdict_scope"] = "application_workload_completion_not_security" if summary["workload_kind"] == "locomo_derived" else "scenario_evidence"
        for metric in METRICS:
            summary[metric] = mean_ci([r[metric] for r in members if r.get(metric) is not None])
        summaries.append(summary)
    for key in sorted({stratum(r) for r in rows}):
        members = [r for r in rows if stratum(r) == key]
        for other in sorted({r["group"] for r in members} - {"full_argus"}):
            for metric in METRICS:
                comparisons.append(dict(paired_difference(members, other, metric), **dict(zip(STRATA, key))))
    result = {"schema": "argus.analysis.v1", "summaries": summaries, "paired_full_minus_other": comparisons,
              "interrupted_measurement_attempts": sum(r.get("interrupted_attempts", 0) for r in rows),
              "ci_unit": "independent run / paired seed block", "ci_method": "percentile bootstrap, 2000 draws, fixed analysis seed",
              "unknown_excluded_from_means_but_reported": True}
    result["stratification"] = list(STRATA)
    result["locomo_results"] = locomo
    result["locomo_scope"] = "QA quality/completion only; source and step hashes bound to runner operation; no security inference"
    atomic(output / "analysis.json", result)
    lines = ["# Argus experiment results", "", "Counts retain FAIL, UNKNOWN and NOT_RUN. No missing run is treated as a pass.", "",
             "| Case | Clients | Workload / connection | Group | PASS | FAIL | UNKNOWN | NOT_RUN |", "|---|---:|---|---|---:|---:|---:|---:|"]
    for s in summaries:
        lines.append("| %s | %s | %s / %s | %s | %s | %s | %s | %s |" % (s["case"], s["scale"], s["workload_kind"], s["connection_mode"], s["group"], *(s["verdicts"][v] for v in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN"))))
    lines += ["", "API Goodput counts expected HTTP completions inside the measurement window. Agent task correctness is separate.",
              "Interrupted attempts are retained and counted separately; only the selected complete window contributes one sample to its original paired block.",
              "Confidence intervals resample independent runs or complete paired seed blocks. Single runs have no interval.",
              "Tail latencies are descriptive; statistics.csv includes the successful latency sample count.",
              "For each scenario, inspect admission, business and receiver evidence separately; an aggregate pass cannot extend their trust boundary."]
    lines += ["", "LoCoMo step PASS means workload completion only. F1, coverage and category-5 abstention are independent quantities; they do not measure delivery security.",
              "LoCoMo conversation-macro F1 includes zero for uncompleted tasks; answered-only F1 and coverage are also retained. QA items are clustered by conversation.",
              "Memory nonempty Goodput counts successful replies containing private leaf memories. HTTP success alone is reported separately.",
              "New/reused connections and status/memory/custom workloads never share a mean or paired contrast. Legacy unspecified traces are separate."]
    qa_rows = [r for r in rows if r.get("workload_kind") == "locomo_derived"]
    if qa_rows:
        lines += ["", "| LoCoMo run | Evidence | Coverage | Conversation F1 | Answered F1 | Abstention |",
                  "|---|---|---:|---:|---:|---:|"]
        for r in qa_rows:
            lines.append("| " + " | ".join(str(r.get(k, "UNKNOWN")) for k in
                         ("run_id", "locomo_evidence", "locomo_coverage", "locomo_conversation_macro_f1", "locomo_answered_f1", "locomo_abstention")) + " |")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # A dependency-free, regenerable plot. Counts include incomplete experiments.
    height = 50 + 28 * len(summaries)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}">', '<rect width="100%" height="100%" fill="white"/>', '<text x="10" y="22" font-family="sans-serif">Verdicts: green PASS / red FAIL / amber UNKNOWN / gray NOT_RUN</text>']
    for i, s in enumerate(summaries):
        y, x = 40 + i * 28, 470
        label = html.escape("%s / n=%d / %s / %s / %s" % (s["case"], s["scale"], s["group"], s["workload_kind"], s["connection_mode"]))
        svg.append(f'<text x="10" y="{y+15}" font-size="12" font-family="sans-serif">{label}</text>')
        total = sum(s["verdicts"].values()) or 1
        for v, color in (("PASS", "#27844b"), ("FAIL", "#b83232"), ("UNKNOWN", "#ba8714"), ("NOT_RUN", "#87929d")):
            width = 460 * s["verdicts"][v] / total
            if width:
                svg.append(f'<rect x="{x}" y="{y}" width="{width}" height="20" fill="{color}"><title>{v}: {s["verdicts"][v]}</title></rect>')
                x += width
    svg.append("</svg>")
    (output / "verdicts.svg").write_text("\n".join(svg), encoding="utf-8")
    return {"result": "ANALYZED", "runs": len(rows), "report": str(output / "report.md")}
