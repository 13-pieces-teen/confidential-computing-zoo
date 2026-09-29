#!/usr/bin/env python3
"""Regenerate scientific figures from independent-run summary statistics."""
import argparse
import math
from pathlib import Path

from common import atomic, read, sha

METRICS = {"api_goodput_rps": "API Goodput (requests/s)", "p95_ms": "Per-run HTTP p95 latency (ms)",
           "memory_nonempty_goodput_rps": "Nonempty private-memory Goodput (requests/s)",
           "tcp_connect_p50_ms": "Per-run median TCP connect time (ms)",
           "tls_handshake_p50_ms": "Per-run median TLS handshake time (ms)",
           "connect_p50_ms": "Per-run median connection and identity check time (ms)",
           "connection_reuse_fraction": "Fraction of requests reusing an existing connection",
           "locomo_conversation_macro_f1": "LoCoMo-derived conversation macro F1 (uncompleted = 0)",
           "locomo_coverage": "LoCoMo-derived answered task coverage",
           "locomo_abstention": "Declared abstention on category-5 questions",
           "locomo_completion_rate": "Answered tasks / all planned tasks",
           "locomo_valid_completion_rate": "Answer + observed injection within deadline / all planned tasks",
           "locomo_injection_rate": "Observed memory injection / all planned tasks",
           "locomo_deadline_miss_rate": "Deadline misses / all planned tasks",
           "locomo_task_mean_ms": "Per-run mean Gateway invocation E2E (valid tasks, ms)",
           "locomo_task_p95_ms": "Per-run p95 Gateway invocation E2E (valid tasks, ms)",
           "locomo_request_mean_ms": "Per-run mean complete successful HTTP response time (ms)",
           "locomo_request_p95_ms": "Per-run p95 complete successful HTTP response time (ms)",
           "locomo_max_in_flight": "Maximum observed concurrent Gateway invocations",
           "locomo_recovery_access_ms": "Recovery command to new successful HTTP access (ms)",
           "locomo_recovery_task_ms": "Recovery command to new valid task completion (ms)",
           "continuous_task_success_rate": "Successful tasks / all planned tasks",
           "continuous_joint_success_rate": "Receipt PASS and task PASS / all planned tasks",
           "continuous_receipt_unknown_rate": "Receipt UNKNOWN / all planned tasks",
           "continuous_deadline_miss_rate": "Deadline misses / all planned tasks",
           "process_cpu_seconds": "Observed process CPU time (s)",
           "sum_process_peak_rss_bytes": "Sum of per-process peak RSS (bytes; upper bound)"}


def context(row):
    return tuple(row.get(key, "unspecified") for key in ("case", "connection_mode", "workload_kind", "workload_spec", "condition", "fault_kind", "fault_scope"))


def observed(row, metric):
    value = row.get(metric, {}).get("mean")
    return type(value) in (int, float) and math.isfinite(value) and row[metric].get("n_runs", 0) > 0


def render(output):
    output = Path(output)
    source = output / "analysis.json"
    result = read(source)
    available = [(key, metric) for key in sorted({context(s) for s in result["summaries"]}) for metric in METRICS
                 if any(context(s) == key and observed(s, metric) for s in result["summaries"])]
    has_locomo_timeline = any(r.get("execution", {}).get("qa_started_at_ms") is not None
                             and any(q.get("started_at_ms") is not None for q in r.get("questions", []))
                             for r in result.get("locomo_results", []))
    if not available and not result.get("continuous_results") and not has_locomo_timeline:
        value = {"result": "NOT_RUN", "reason": "no complete quantitative observations", "files": []}
        atomic(output / "figures.json", value)
        return value
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    files = []
    figures = output / "figures"; figures.mkdir(exist_ok=True)
    for key, metric in available:
        case, connection_mode, workload_kind, workload_spec, condition, fault_kind, fault_scope = key
        fig, ax = plt.subplots(figsize=(7, 4.3), layout="constrained")
        for group in sorted({s["group"] for s in result["summaries"]}):
            rows = sorted((s for s in result["summaries"] if context(s) == key and s["group"] == group and observed(s, metric)), key=lambda s: s["scale"])
            if not rows: continue
            line, = ax.plot([s["scale"] for s in rows], [s[metric]["mean"] for s in rows], marker="o", label=group)
            for s in rows:
                stats = s[metric]
                if all(type(stats.get(k)) in (int, float) and math.isfinite(stats[k]) for k in ("low", "high")):
                    ax.errorbar(s["scale"], stats["mean"], yerr=[[max(0, stats["mean"]-stats["low"])] , [max(0, stats["high"]-stats["mean"])]], color=line.get_color(), capsize=3)
                ax.annotate("n=" + str(stats["n_runs"]), (s["scale"], stats["mean"]), xytext=(4, 5), textcoords="offset points", fontsize=7)
        ax.set(xlabel="Independent clients", ylabel=METRICS[metric], title=f"{case}\n{workload_kind} / {connection_mode} / {condition}")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.2); ax.legend(fontsize=8)
        for extension in ("svg", "pdf"):
            suffix = "-" + "-".join((workload_kind, connection_mode, workload_spec[:12], condition, fault_kind, fault_scope))
            path = figures / (case + suffix + "-" + metric + "." + extension)
            fig.savefig(path)
            files.append({"path": str(path.relative_to(output)), "sha256": sha(path)})
        plt.close(fig)
    from continuous_plot import render as render_continuous
    files.extend(render_continuous(output, result, plt))
    from locomo_analysis import render as render_locomo
    files.extend(render_locomo(output, result, plt))
    value = {"result": "GENERATED", "analysis_sha256": sha(source), "files": files,
             "uncertainty": "95% independent-run bootstrap intervals from analysis.json; none for n=1",
             "tail_latency": "mean of per-run p95; request counts remain in statistics.csv"}
    value["stratification"] = "separate figures for workload, connections, fault scope and LoCoMo source/selection/execution protocol/observed models; no cross-stratum pooling"
    value["qa_scope"] = "LoCoMo application completion/latency/availability do not establish reception security; F1 is auxiliary; CI uses runs, not questions"
    atomic(output / "figures.json", value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--output", required=True)
    a = p.parse_args(); print(__import__("json").dumps(render(a.output), indent=2))


if __name__ == "__main__": main()
