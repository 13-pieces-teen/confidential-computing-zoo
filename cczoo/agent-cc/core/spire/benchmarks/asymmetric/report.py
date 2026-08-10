#!/usr/bin/env python3
"""Aggregate Argus benchmark JSONL into machine and reader-facing reports."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from collector import parse_prometheus


def percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values if value is not None and math.isfinite(value))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    samples = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return {
        "count": len(samples),
        "avg": sum(samples) / len(samples) if samples else None,
        "p50": percentile(samples, 0.50),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
        "max": max(samples) if samples else None,
    }


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


def resource_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    component_values: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    host_cpu: list[float] = []
    host_network_received: list[float] = []
    host_network_transmitted: list[float] = []
    host_disk_read: list[float] = []
    host_disk_written: list[float] = []
    host_tcp_retransmissions: list[float] = []
    target_connections: list[float] = []
    guest_target_connections: list[float] = []
    collector_errors = 0
    svid_serials: dict[str, list[str]] = defaultdict(list)
    metric_first: dict[str, float] = {}
    metric_last: dict[str, float] = {}
    for record in records:
        collector_errors += len(record.get("errors", []))
        if record.get("host", {}).get("cpu_percent") is not None:
            host_cpu.append(record["host"]["cpu_percent"])
        host = record.get("host", {})
        network_delta = host.get("network_delta") or {}
        disk_delta = host.get("disk_delta") or {}
        if network_delta.get("received_bytes") is not None:
            host_network_received.append(network_delta["received_bytes"])
        if network_delta.get("transmitted_bytes") is not None:
            host_network_transmitted.append(network_delta["transmitted_bytes"])
        if disk_delta.get("read_bytes") is not None:
            host_disk_read.append(disk_delta["read_bytes"])
        if disk_delta.get("written_bytes") is not None:
            host_disk_written.append(disk_delta["written_bytes"])
        if host.get("tcp_retransmissions_delta") is not None:
            host_tcp_retransmissions.append(host["tcp_retransmissions_delta"])
        if record.get("connections", {}).get("target_total") is not None:
            target_connections.append(record["connections"]["target_total"])
        if record.get("guest_connections", {}).get("target_total") is not None:
            guest_target_connections.append(record["guest_connections"]["target_total"])
        for location in ("host_containers", "guest_containers"):
            for component in record.get(location, []):
                label = component.get("label", "unknown")
                for field in ("cpu_percent", "memory_used_bytes", "pids", "fd_count"):
                    value = component.get(field)
                    if value is not None:
                        component_values[label][field].append(float(value))
        for label, status in record.get("svids", {}).items():
            serial = status.get("serial_number")
            if serial and (not svid_serials[label] or svid_serials[label][-1] != serial):
                svid_serials[label].append(serial)
        for endpoint, samples in record.get("metrics", {}).items():
            for name, value in samples.items():
                key = f"{endpoint}:{name}"
                metric_first.setdefault(key, float(value))
                metric_last[key] = float(value)
    components: dict[str, Any] = {}
    for label, fields in component_values.items():
        components[label] = {
            "cpu_percent": distribution(fields.get("cpu_percent", [])),
            "memory_used_bytes": distribution(fields.get("memory_used_bytes", [])),
            "pids": distribution(fields.get("pids", [])),
            "fd_count": distribution(fields.get("fd_count", [])),
        }
    return {
        "samples": len(records),
        "collector_errors": collector_errors,
        "host_cpu_percent": distribution(host_cpu),
        "host_network_received_bytes_per_interval": distribution(host_network_received),
        "host_network_transmitted_bytes_per_interval": distribution(host_network_transmitted),
        "host_disk_read_bytes_per_interval": distribution(host_disk_read),
        "host_disk_written_bytes_per_interval": distribution(host_disk_written),
        "host_tcp_retransmissions_per_interval": distribution(host_tcp_retransmissions),
        "target_connections": distribution(target_connections),
        "guest_target_connections": distribution(guest_target_connections),
        "components": components,
        "svid_serials": dict(svid_serials),
        "svid_rotations": {
            label: max(0, len(serials) - 1) for label, serials in svid_serials.items()
        },
        "metric_deltas": {
            key: metric_last[key] - value for key, value in metric_first.items()
            if key in metric_last
        },
    }


def aggregate_case(case_directory: Path) -> dict[str, Any]:
    metadata_path = case_directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    request_records = read_jsonl(case_directory / "requests.jsonl")
    resource_records = read_jsonl(case_directory / "resources.jsonl")
    requests = [record for record in request_records if record.get("type") == "request"]
    summaries = [record for record in request_records if record.get("type") == "summary"]
    transports = [
        record for record in request_records
        if record.get("type") == "spiffe_transport"
        and record.get("outcome") == "response_headers"
    ]
    successful = [record for record in requests if record.get("ok") is True]
    reused_samples = [
        record.get("reused_connection") for record in successful
        if record.get("reused_connection") is not None
    ]
    latest_summary = summaries[-1] if summaries else {}
    timed_resource_records = resource_records
    if latest_summary.get("started_unix_ms") is not None and latest_summary.get("duration_seconds") is not None:
        window_start = float(latest_summary["started_unix_ms"])
        window_end = window_start + float(latest_summary["duration_seconds"]) * 1000
        in_window = [
            record for record in resource_records
            if window_start <= float(record.get("timestamp_unix_ms", -1)) <= window_end
        ]
        if in_window:
            timed_resource_records = in_window
    total_requests = int(latest_summary.get("requests", len(requests)))
    total_successful = int(latest_summary.get("succeeded", len(successful)))
    total_failed = int(latest_summary.get("failed", total_requests - total_successful))
    resources = resource_summary(timed_resource_records)
    guard_requests_delta = sum(
        value for name, value in resources["metric_deltas"].items()
        if name.startswith("guard:argus_guard_requests_total")
    )
    trustee_requests_delta = sum(
        value for name, value in resources["metric_deltas"].items()
        if name.startswith("spire-server:spire_server_argus_nodeattestor_trustee_requests")
    )
    return {
        "case": case_directory.name,
        "experiment": metadata.get("experiment", case_directory.name.split("-", 1)[0].upper()),
        "profile": metadata.get("profile", latest_summary.get("experiment_profile")),
        "requested_qps": metadata.get("requested_qps", latest_summary.get("requested_qps")),
        "achieved_qps": latest_summary.get("achieved_qps"),
        "concurrency": metadata.get("concurrency", latest_summary.get("concurrency")),
        "requests": total_requests,
        "recorded_request_samples": len(requests),
        "successful": total_successful,
        "failed": total_failed,
        "success_rate": total_successful / total_requests if total_requests else None,
        "request_latency_ms": distribution(record.get("duration_ms") for record in successful),
        "mtls_handshake_ms": distribution(record.get("handshake_ms") for record in successful),
        "connection_reuse_ratio": (
            sum(1 for value in reused_samples if value) / len(reused_samples)
            if reused_samples else None
        ),
        "transport_decision_correlation_rate": (
            len(transports) / total_successful
            if metadata.get("mode") == "guarded" and total_successful else None
        ),
        "guard_decision_ms": distribution(record.get("guard_decision_ms") for record in transports),
        "guard_to_send_ms": distribution(record.get("guard_to_send_ms") for record in transports),
        "transport_headers_ms": distribution(record.get("transport_headers_ms") for record in transports),
        "guarded_request_headers_ms": distribution(
            record.get("guarded_request_headers_ms") for record in transports
        ),
        "guard_requests_observed_delta": guard_requests_delta,
        "trustee_requests_observed_delta": trustee_requests_delta,
        "resource": resources,
    }


def read_prometheus_snapshot(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    return parse_prometheus(path.read_text(encoding="utf-8"))


def metric_sum(
    samples: dict[str, float], prefix: str, required_labels: Iterable[str] = ()
) -> float | None:
    labels = tuple(required_labels)
    matches = [
        value for name, value in samples.items()
        if name.startswith(prefix) and all(label in name for label in labels)
    ]
    return sum(matches) if matches else None


def safe_ratio(
    numerator: float | None, denominator: float | None, multiplier: float = 1.0
) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * multiplier


def amortization(cases: list[dict[str, Any]], before: dict[str, float], after: dict[str, float]) -> dict[str, Any]:
    business_requests = sum(
        case["successful"] for case in cases if case["experiment"] in {"E5", "E6"}
    )
    successful_attestations_before = metric_sum(
        before,
        "spire_server_argus_nodeattestor_attempts",
        ('result="success"', 'side="server"'),
    )
    successful_attestations_after = metric_sum(
        after,
        "spire_server_argus_nodeattestor_attempts",
        ('result="success"', 'side="server"'),
    )
    trustee_before = metric_sum(
        before,
        "spire_server_argus_nodeattestor_trustee_requests",
        ('result="success"',),
    )
    trustee_after = metric_sum(
        after,
        "spire_server_argus_nodeattestor_trustee_requests",
        ('result="success"',),
    )
    rotations = sum(
        case["resource"]["svid_rotations"].get("openviking", 0)
        for case in cases if case["experiment"] == "E6"
    )
    new_attestations = (
        max(0.0, successful_attestations_after - successful_attestations_before)
        if successful_attestations_before is not None and successful_attestations_after is not None
        else None
    )
    new_trustee_requests = (
        max(0.0, trustee_after - trustee_before)
        if trustee_before is not None and trustee_after is not None
        else None
    )
    return {
        "successful_business_requests": business_requests,
        "successful_attestations_observed": successful_attestations_after,
        "successful_trustee_requests_observed": trustee_after,
        "new_attestations_during_business_benchmark": new_attestations,
        "new_trustee_requests_during_business_benchmark": new_trustee_requests,
        "openviking_svid_rotations": rotations,
        "business_requests_per_attestation": safe_ratio(
            business_requests, successful_attestations_after
        ),
        "trustee_requests_per_1000_business_requests": safe_ratio(
            trustee_after, business_requests, 1000
        ),
        "trustee_requests_per_svid_rotation": safe_ratio(new_trustee_requests, rotations),
    }


def display(value: Any, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(run_directory: Path, cases: list[dict[str, Any]], e7: dict[str, Any]) -> str:
    lines = [
        "# Argus 非对称 SPIFFE 远程评估报告",
        "",
        "> Attestation profile: Mock Evidence Provider + Mock Trustee",
        ">",
        "> 本报告只描述软件数据路径、资源、容量、SVID 轮换和证明调用次数，不代表真实 TDX Quote/QGS 性能。",
        "",
        f"Run directory: `{run_directory}`",
        "",
        "## E3-E6 汇总",
        "",
        "| Case | Profile | QPS | Concurrency | Requests | Success | P50 ms | P95 ms | P99 ms | Connections peak |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        latency = case["request_latency_ms"]
        connections = case["resource"]["guest_target_connections"]
        if connections["count"] == 0:
            connections = case["resource"]["target_connections"]
        lines.append(
            "| {case} | {profile} | {qps} | {concurrency} | {requests} | {success} | {p50} | {p95} | {p99} | {connections} |".format(
                case=case["case"],
                profile=case["profile"] or "N/A",
                qps=display(case["achieved_qps"]),
                concurrency=display(case["concurrency"], 0),
                requests=case["requests"],
                success=display(case["success_rate"] * 100 if case["success_rate"] is not None else None),
                p50=display(latency["p50"]),
                p95=display(latency["p95"]),
                p99=display(latency["p99"]),
                connections=display(connections["max"], 0),
            )
        )
    lines.extend([
        "",
        "## E4 mTLS 连接",
        "",
        "| Case | Profile | Handshake P50 ms | Handshake P95 ms | Handshake P99 ms | Connection reuse |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for case in cases:
        if case["experiment"] != "E4":
            continue
        handshake = case["mtls_handshake_ms"]
        reuse = case["connection_reuse_ratio"]
        lines.append(
            "| {case} | {profile} | {p50} | {p95} | {p99} | {reuse} |".format(
                case=case["case"],
                profile=case["profile"] or "N/A",
                p50=display(handshake["p50"]),
                p95=display(handshake["p95"]),
                p99=display(handshake["p99"]),
                reuse=display(reuse * 100 if reuse is not None else None),
            )
        )
    lines.extend([
        "",
        "## E6 SVID 轮换",
        "",
        "| Case | OpenViking rotations | Requests failed | Latency max ms | Trustee request delta |",
        "|---|---:|---:|---:|---:|",
    ])
    for case in cases:
        if case["experiment"] != "E6":
            continue
        lines.append(
            "| {case} | {rotations} | {failed} | {latency} | {trustee} |".format(
                case=case["case"],
                rotations=case["resource"]["svid_rotations"].get("openviking", 0),
                failed=case["failed"],
                latency=display(case["request_latency_ms"]["max"]),
                trustee=display(case["trustee_requests_observed_delta"], 0),
            )
        )
    lines.extend([
        "",
        "## 资源峰值",
        "",
        "| Case | Component | CPU avg % | CPU P95 % | CPU peak % | RSS peak MiB | FDs peak | PIDs peak |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for case in cases:
        for component, resources in sorted(case["resource"]["components"].items()):
            lines.append(
                "| {case} | {component} | {cpu_avg} | {cpu_p95} | {cpu_peak} | {rss} | {fds} | {pids} |".format(
                    case=case["case"],
                    component=component,
                    cpu_avg=display(resources["cpu_percent"]["avg"]),
                    cpu_p95=display(resources["cpu_percent"]["p95"]),
                    cpu_peak=display(resources["cpu_percent"]["max"]),
                    rss=display(
                        resources["memory_used_bytes"]["max"] / 1024 / 1024
                        if resources["memory_used_bytes"]["max"] is not None else None
                    ),
                    fds=display(resources["fd_count"]["max"], 0),
                    pids=display(resources["pids"]["max"], 0),
                )
            )
    lines.extend([
        "",
        "## E7 证明摊销",
        "",
        f"- 成功业务请求：{e7['successful_business_requests']}",
        f"- 观测到的成功 Node Attestation：{display(e7['successful_attestations_observed'], 0)}",
        f"- 业务测试期间新增 Node Attestation：{display(e7['new_attestations_during_business_benchmark'], 0)}",
        f"- 业务测试期间新增 Trustee 请求：{display(e7['new_trustee_requests_during_business_benchmark'], 0)}",
        f"- OpenViking SVID 轮换：{e7['openviking_svid_rotations']}",
        f"- business requests / attestation：{display(e7['business_requests_per_attestation'])}",
        f"- Trustee requests / 1000 business requests：{display(e7['trustee_requests_per_1000_business_requests'])}",
        f"- Trustee requests / SVID rotation：{display(e7['trustee_requests_per_svid_rotation'])}",
        "",
        "## 结果边界",
        "",
        "- `diagnostic_mtls_only` 只作为传输基线，不是正式业务结果。",
        "- `null`/`N/A` 表示缺少实测数据，不使用配置值补齐。",
        "- 真实 Quote、QGS、production Trustee、多 OpenClaw 和多 Agent Service 容量均不在本报告范围。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    arguments = parser.parse_args()
    run_directory = Path(arguments.run_dir).resolve()
    case_root = run_directory / "cases"
    cases = [
        aggregate_case(path)
        for path in sorted(case_root.iterdir())
        if path.is_dir()
    ] if case_root.exists() else []
    before = read_prometheus_snapshot(run_directory / "spire-metrics-before.prom")
    after = read_prometheus_snapshot(run_directory / "spire-metrics-after.prom")
    e7 = amortization(cases, before, after)
    summary = {
        "schema_version": "argus-asymmetric-evaluation-v1",
        "attestation_profile": "mock_ra_mock_trustee",
        "run_directory": str(run_directory),
        "cases": cases,
        "e7": e7,
    }
    (run_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_directory / "report.md").write_text(
        render_markdown(run_directory, cases, e7),
        encoding="utf-8",
    )
    print(run_directory / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
