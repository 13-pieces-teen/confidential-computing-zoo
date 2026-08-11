#!/usr/bin/env python3
"""Generate the E8 Agent-task summary from task receipts and resource samples."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

PARENT_BENCHMARK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PARENT_BENCHMARK))
from collector import parse_prometheus  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {error}") from error
    return records


def percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not ordered:
        return None
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    samples = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return {
        "count": len(samples),
        "p50": percentile(samples, 0.50),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99) if len(samples) > 100 else None,
        "max": max(samples) if samples else None,
    }


def safe_ratio(numerator: float | int | None, denominator: float | int | None, multiplier: float = 1.0) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator) * multiplier


def resource_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    collector_errors = 0
    for record in records:
        collector_errors += len(record.get("errors", []))
        for location in ("host_containers", "guest_containers"):
            for component in record.get(location, []):
                label = component.get("label", "unknown")
                for field in ("cpu_percent", "memory_used_bytes", "pids", "fd_count"):
                    if component.get(field) is not None:
                        values[label][field].append(float(component[field]))
    return {
        "samples": len(records),
        "collector_errors": collector_errors,
        "components": {
            label: {field: distribution(samples) for field, samples in fields.items()}
            for label, fields in sorted(values.items())
        },
    }


def unit_summary(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        grouped[str(task.get("unit_id") or "unknown")].append(task)
    units = []
    for unit_id, records in sorted(grouped.items()):
        completed = [record for record in records if record.get("status") == "completed"]
        starts = [record.get("started_unix_ms") for record in records if record.get("started_unix_ms") is not None]
        ends = [record.get("finished_unix_ms") for record in records if record.get("finished_unix_ms") is not None]
        duration_minutes = (max(ends) - min(starts)) / 60_000 if starts and ends and max(ends) > min(starts) else None
        units.append({
            "unit_id": unit_id,
            "launched": len(records),
            "completed": len(completed),
            "success_rate": safe_ratio(len(completed), len(records)),
            "tasks_per_minute": safe_ratio(len(completed), duration_minutes),
            "e2e_ms": distribution(record.get("agent_task_e2e_ms") for record in completed),
        })
    return units


def aggregate_case(case_directory: Path) -> dict[str, Any]:
    metadata_path = case_directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    tasks = [record for record in read_jsonl(case_directory / "tasks.jsonl") if record.get("measured") is True]
    completed = [record for record in tasks if record.get("status") == "completed"]
    failed = [record for record in tasks if record.get("status") != "completed"]
    starts = [record.get("started_unix_ms") for record in tasks if record.get("started_unix_ms") is not None]
    ends = [record.get("finished_unix_ms") for record in tasks if record.get("finished_unix_ms") is not None]
    duration_minutes = (max(ends) - min(starts)) / 60_000 if starts and ends and max(ends) > min(starts) else None
    units = unit_summary(tasks)
    unit_rates = [unit["tasks_per_minute"] for unit in units if unit["tasks_per_minute"] is not None]
    if not unit_rates or max(unit_rates) == 0:
        fairness = None
    else:
        fairness = min(unit_rates) / max(unit_rates)
    failures = Counter(str(record.get("failure_stage") or "unknown") for record in failed)
    errors = Counter(str(record.get("error_class") or "error") for record in failed)
    failure_stage_elapsed: dict[str, list[float]] = defaultdict(list)
    for record in failed:
        elapsed = record.get("elapsed_ms")
        if elapsed is not None:
            failure_stage_elapsed[str(record.get("failure_stage") or "unknown")].append(float(elapsed))
    resources = resource_summary(read_jsonl(case_directory / "resources.jsonl"))
    return {
        "case": case_directory.name,
        "openclaw_units": int(metadata.get("openclaw_units", len(units))),
        "launched": len(tasks),
        "completed": len(completed),
        "failed": len(tasks) - len(completed),
        "success_rate": safe_ratio(len(completed), len(tasks)),
        "tasks_per_minute": safe_ratio(len(completed), duration_minutes),
        "measurement_minutes": duration_minutes,
        "scaling_efficiency": None,
        "fairness_ratio": fairness,
        "task_finalization_ms": distribution(record.get("elapsed_ms") for record in tasks),
        "failed_task_elapsed_ms": distribution(record.get("elapsed_ms") for record in failed),
        "agent_task_e2e_ms": distribution(record.get("agent_task_e2e_ms") for record in completed),
        "agent_turn_ms": distribution(record.get("agent_turn_ms") for record in completed),
        "capture_first_observed_ms": distribution(record.get("capture_first_observed_ms") for record in completed),
        "commit_to_archive_ms": distribution(record.get("commit_to_archive_ms") for record in completed),
        "input_tokens": sum(record.get("input_tokens") or 0 for record in completed) or None,
        "output_tokens": sum(record.get("output_tokens") or 0 for record in completed) or None,
        "failure_stages": dict(sorted(failures.items())),
        "failure_stage_elapsed_ms": {
            stage: distribution(samples) for stage, samples in sorted(failure_stage_elapsed.items())
        },
        "error_classes": dict(sorted(errors.items())),
        "generation_provider_errors": failures.get("openclaw_generation_provider", 0),
        "archive_provider_errors": failures.get("openviking_archive_provider", 0),
        "units": units,
        "resource": resources,
    }


def read_snapshot(path: Path) -> dict[str, float]:
    return parse_prometheus(path.read_text(encoding="utf-8")) if path.exists() else {}


def metric_sum(samples: dict[str, float], prefix: str, labels: tuple[str, ...]) -> float | None:
    values = [value for name, value in samples.items() if name.startswith(prefix) and all(label in name for label in labels)]
    return sum(values) if values else None


def delta(before: float | None, after: float | None) -> tuple[float | None, bool]:
    if before is None or after is None:
        return None, False
    if after < before:
        return None, True
    return after - before, False


def control_plane(run_directory: Path, completed_tasks: int) -> dict[str, Any]:
    before = read_snapshot(run_directory / "spire-metrics-before.prom")
    after = read_snapshot(run_directory / "spire-metrics-after.prom")
    attestation_before = metric_sum(before, "spire_server_argus_nodeattestor_attempts", ('result="success"', 'side="server"'))
    attestation_after = metric_sum(after, "spire_server_argus_nodeattestor_attempts", ('result="success"', 'side="server"'))
    trustee_before = metric_sum(before, "spire_server_argus_nodeattestor_trustee_requests", ('result="success"',))
    trustee_after = metric_sum(after, "spire_server_argus_nodeattestor_trustee_requests", ('result="success"',))
    attestation_delta, attestation_reset = delta(attestation_before, attestation_after)
    trustee_delta, trustee_reset = delta(trustee_before, trustee_after)
    return {
        "left_active_agents_at_start": None,
        "right_active_agents_at_start": None,
        "left_node_attestation_delta": None,
        "right_node_attestation_delta": attestation_delta,
        "left_trustee_request_delta": None,
        "right_trustee_request_delta": trustee_delta,
        "completed_tasks_per_new_right_node_attestation": safe_ratio(completed_tasks, attestation_delta),
        "right_trustee_requests_per_1000_completed_tasks": safe_ratio(trustee_delta, completed_tasks, 1000),
        "counter_reset_observed": attestation_reset or trustee_reset,
        "unavailable_reason": "current Prometheus profile does not expose left x509pop active-agent/attestation counters",
    }


def formal_matrix_status(cases: list[dict[str, Any]]) -> dict[str, Any]:
    required = ("C1", "C2", "C4", "C8")
    observed = {str(case.get("case", "")).upper(): case for case in cases}
    missing = [name for name in required if name not in observed]
    empty = [name for name in required if name in observed and int(observed[name].get("launched", 0)) <= 0]
    return {
        "complete": not missing and not empty,
        "required_cases": list(required),
        "missing_cases": missing,
        "empty_cases": empty,
    }


def apply_scaling(cases: list[dict[str, Any]]) -> None:
    baseline = next((case for case in cases if case["case"].upper() == "C1"), None)
    baseline_rate = baseline.get("tasks_per_minute") if baseline else None
    for case in cases:
        if not case["case"].upper().startswith("C"):
            case["scaling_efficiency"] = None
            continue
        case["scaling_efficiency"] = safe_ratio(
            case.get("tasks_per_minute"),
            case.get("openclaw_units", 0) * baseline_rate if baseline_rate else None,
        )


def show(value: Any, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def render_markdown(run_directory: Path, cases: list[dict[str, Any]], plane: dict[str, Any]) -> str:
    formal = formal_matrix_status(cases)
    formal_label = "complete" if formal["complete"] else "incomplete"
    formal_detail = ", ".join(formal["missing_cases"] + formal["empty_cases"]) or "none"
    lines = [
        "# Argus 多 OpenClaw 真实 LLM Agent 任务评估报告",
        "",
        "> 单 OpenViking、共享 x509pop Agent、Mock Evidence Provider + Mock Trustee。",
        "> 本报告是当前配置下的探索性快照，不代表生产容量或真实 Quote/QGS 性能。",
        "",
        f"Run directory: `{run_directory}`",
        f"Formal matrix: `{formal_label}`（missing/empty: {formal_detail}）",
        "",
        "## Case 汇总",
        "",
        "| Case | OpenClaw | 完成/启动 | Tasks/min | 成功率 % | Completed E2E P50/P95/max ms | Agent P50 ms | Archive P50 ms | Gen provider errors | Archive provider errors | Fairness | Scaling |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        e2e = case["agent_task_e2e_ms"]
        lines.append(
            f"| {case['case']} | {case['openclaw_units']} | {case['completed']}/{case['launched']} | "
            f"{show(case['tasks_per_minute'])} | {show((case['success_rate'] or 0) * 100)} | "
            f"{show(e2e['p50'])}/{show(e2e['p95'])}/{show(e2e['max'])} | "
            f"{show(case['agent_turn_ms']['p50'])} | {show(case['commit_to_archive_ms']['p50'])} | "
            f"{case['generation_provider_errors']} | {case['archive_provider_errors']} | "
            f"{show(case['fairness_ratio'])} | {show(case['scaling_efficiency'])} |"
        )
    lines.extend([
        "",
        "## 全任务最终耗时",
        "",
        "| Case | 全部任务 P50/P95/max ms | 失败任务 P50/P95/max ms |",
        "|---|---:|---:|",
    ])
    for case in cases:
        final = case["task_finalization_ms"]
        failed = case["failed_task_elapsed_ms"]
        lines.append(
            f"| {case['case']} | {show(final['p50'])}/{show(final['p95'])}/{show(final['max'])} | "
            f"{show(failed['p50'])}/{show(failed['p95'])}/{show(failed['max'])} |"
        )
    lines.extend(["", "## Per-unit", "", "| Case | Unit | 完成/启动 | Tasks/min | 成功率 % | E2E P95 ms |", "|---|---|---:|---:|---:|---:|"])
    for case in cases:
        for unit in case["units"]:
            lines.append(
                f"| {case['case']} | {unit['unit_id']} | {unit['completed']}/{unit['launched']} | "
                f"{show(unit['tasks_per_minute'])} | {show((unit['success_rate'] or 0) * 100)} | {show(unit['e2e_ms']['p95'])} |"
            )
    lines.extend(["", "## 资源峰值", "", "| Case | Component | CPU peak % | RSS peak MiB | FDs peak | PIDs peak |", "|---|---|---:|---:|---:|---:|"])
    for case in cases:
        for component, fields in case["resource"]["components"].items():
            rss = fields.get("memory_used_bytes", {}).get("max")
            lines.append(
                f"| {case['case']} | {component} | {show(fields.get('cpu_percent', {}).get('max'))} | "
                f"{show(rss / 1024 / 1024 if rss is not None else None)} | "
                f"{show(fields.get('fd_count', {}).get('max'), 0)} | {show(fields.get('pids', {}).get('max'), 0)} |"
            )
    lines.extend(["", "## 失败阶段", ""])
    if any(case["failure_stages"] for case in cases):
        for case in cases:
            for stage, count in case["failure_stages"].items():
                elapsed = case["failure_stage_elapsed_ms"].get(stage, {})
                lines.append(f"- {case['case']} `{stage}`: {count}，耗时 P50/P95/max={show(elapsed.get('p50'))}/{show(elapsed.get('p95'))}/{show(elapsed.get('max'))} ms")
    else:
        lines.append("- 无正式任务失败。")
    lines.extend([
        "",
        "## 控制面观测",
        "",
        f"- 右侧新增 Node Attestation：{show(plane['right_node_attestation_delta'], 0)}",
        f"- 右侧新增 Trustee 请求：{show(plane['right_trustee_request_delta'], 0)}",
        f"- 完成任务 / 新增右侧 Node Attestation：{show(plane['completed_tasks_per_new_right_node_attestation'])}",
        f"- 右侧 Trustee 请求 / 1000 完成任务：{show(plane['right_trustee_requests_per_1000_completed_tasks'])}",
        f"- 左侧指标：N/A（{plane['unavailable_reason']}）",
        "",
        "## 结果边界",
        "",
        f"- C1/C2/C4/C8 formal matrix：`{formal_label}`；不完整时不得据此声明正式容量或扩展效率。",
        "- Completed E2E 仅统计成功任务；全任务最终耗时和失败阶段耗时包含失败收据。",
        "- `null`/`N/A` 表示没有可验证的测量值，不使用配置值补齐。",
        "- OpenClaw generation Provider 与 OpenViking archive 错误按失败阶段分别统计；无法证明来源时不推断。",
        "- 多个 OpenClaw 共享当前 x509pop Agent 和业务 SPIFFE ID，不代表多个独立身份域。",
        "- 当前 RA/Trustee 仍为 Mock Profile。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    arguments = parser.parse_args()
    run_directory = Path(arguments.run_dir).resolve()
    case_root = run_directory / "cases"
    cases = [aggregate_case(path) for path in sorted(case_root.iterdir()) if path.is_dir()] if case_root.exists() else []
    apply_scaling(cases)
    completed_tasks = sum(case["completed"] for case in cases)
    plane = control_plane(run_directory, completed_tasks)
    summary = {
        "schema_version": "argus-e8-agent-task-summary-v2",
        "attestation_profile": "mock_ra_mock_trustee",
        "run_directory": str(run_directory),
        "cases": cases,
        "formal_matrix": formal_matrix_status(cases),
        "control_plane": plane,
    }
    (run_directory / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_directory / "report.md").write_text(render_markdown(run_directory, cases, plane), encoding="utf-8")
    print(run_directory / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
