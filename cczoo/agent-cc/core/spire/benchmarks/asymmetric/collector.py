#!/usr/bin/env python3
"""Lightweight JSONL resource collector for remote Argus benchmarks."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterable


SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}
PROMETHEUS_PREFIXES = (
    "argus_guard_",
    "spire_server_argus_nodeattestor_",
    "spire_agent_argus_nodeattestor_",
    "go_goroutines",
    "process_",
)


def parse_size(value: str) -> int | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*", value)
    if not match:
        return None
    multiplier = SIZE_UNITS.get(match.group(2).upper())
    if multiplier is None:
        return None
    return round(float(match.group(1)) * multiplier)


def parse_percentage(value: str) -> float | None:
    try:
        return float(value.strip().removesuffix("%"))
    except ValueError:
        return None


def parse_io_pair(value: str) -> dict[str, int | None]:
    left, separator, right = value.partition("/")
    if not separator:
        return {"read_bytes": None, "write_bytes": None}
    return {"read_bytes": parse_size(left), "write_bytes": parse_size(right)}


def parse_docker_stats_line(line: str, labels: dict[str, str]) -> dict[str, Any]:
    raw = json.loads(line)
    name = raw.get("Name") or raw.get("Container") or "unknown"
    memory_used, _, memory_limit = str(raw.get("MemUsage", "")).partition("/")
    return {
        "label": labels.get(name, name),
        "container": name,
        "cpu_percent": parse_percentage(str(raw.get("CPUPerc", ""))),
        "memory_percent": parse_percentage(str(raw.get("MemPerc", ""))),
        "memory_used_bytes": parse_size(memory_used),
        "memory_limit_bytes": parse_size(memory_limit),
        "network": parse_io_pair(str(raw.get("NetIO", ""))),
        "block_io": parse_io_pair(str(raw.get("BlockIO", ""))),
        "pids": int(raw["PIDs"]) if str(raw.get("PIDs", "")).isdigit() else None,
    }


def run_command(command: list[str], timeout: float = 15) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.stdout


def docker_stats(command_prefix: list[str], containers: dict[str, str]) -> list[dict[str, Any]]:
    if not containers:
        return []
    # OpenSSH joins remote command arguments through a shell. Preserve the
    # required space in Docker's Go template when the command prefix is SSH.
    stats_format = "'{{json .}}'" if command_prefix and command_prefix[0] == "ssh" else "{{json .}}"
    output = run_command(
        [
            *command_prefix,
            "stats",
            "--no-stream",
            "--format",
            stats_format,
            *containers.values(),
        ]
    )
    labels = {container: label for label, container in containers.items()}
    samples = [
        parse_docker_stats_line(line, labels)
        for line in output.splitlines()
        if line.strip()
    ]
    for sample in samples:
        try:
            sample["fd_count"] = container_fd_count(command_prefix, sample["container"])
        except (OSError, ValueError, subprocess.SubprocessError):
            sample["fd_count"] = None
    return samples


def container_fd_count(command_prefix: list[str], container: str) -> int:
    raw_pid = run_command([
        *command_prefix,
        "inspect",
        "--format",
        "{{.State.Pid}}",
        container,
    ], timeout=10).strip()
    pid = int(raw_pid)
    if pid <= 0:
        raise ValueError(f"container {container} is not running")
    fd_path = f"/proc/{pid}/fd"
    if command_prefix and command_prefix[0] == "ssh":
        sudo_index = command_prefix.index("sudo")
        ssh_only = command_prefix[:sudo_index]
        output = run_command([
            *ssh_only,
            "sudo",
            "-n",
            "find",
            fd_path,
            "-mindepth", "1",
            "-maxdepth", "1",
        ], timeout=10)
        return len([line for line in output.splitlines() if line.strip()])
    return len(os.listdir(fd_path))


def parse_prometheus(text: str) -> dict[str, float]:
    samples: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(None, 1)
        if len(parts) != 2:
            continue
        name = parts[0].split("{", 1)[0]
        if not name.startswith(PROMETHEUS_PREFIXES):
            continue
        try:
            samples[parts[0]] = float(parts[1])
        except ValueError:
            continue
    return samples


def fetch_prometheus(url: str) -> dict[str, float]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return parse_prometheus(response.read().decode("utf-8", errors="replace"))


def read_key_values(path: str) -> dict[str, int]:
    values: dict[str, int] = {}
    with open(path, encoding="utf-8") as source:
        for line in source:
            key, separator, raw = line.partition(":")
            if not separator:
                continue
            number = raw.strip().split()[0]
            if number.isdigit():
                values[key] = int(number) * 1024
    return values


def cpu_totals() -> tuple[int, int]:
    with open("/proc/stat", encoding="utf-8") as source:
        values = [int(value) for value in source.readline().split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def network_totals() -> tuple[int, int]:
    received = 0
    transmitted = 0
    with open("/proc/net/dev", encoding="utf-8") as source:
        for line in source.readlines()[2:]:
            _, separator, raw = line.partition(":")
            if not separator:
                continue
            fields = raw.split()
            received += int(fields[0])
            transmitted += int(fields[8])
    return received, transmitted


def disk_totals() -> tuple[int, int]:
    block_devices = set(os.listdir("/sys/block"))
    read_bytes = 0
    written_bytes = 0
    with open("/proc/diskstats", encoding="utf-8") as source:
        for line in source:
            fields = line.split()
            if len(fields) < 14 or fields[2] not in block_devices:
                continue
            read_bytes += int(fields[5]) * 512
            written_bytes += int(fields[9]) * 512
    return read_bytes, written_bytes


def tcp_retransmissions() -> int | None:
    with open("/proc/net/snmp", encoding="utf-8") as source:
        lines = source.readlines()
    for index in range(len(lines) - 1):
        if not lines[index].startswith("Tcp:") or not lines[index + 1].startswith("Tcp:"):
            continue
        names = lines[index].split()[1:]
        values = lines[index + 1].split()[1:]
        if "RetransSegs" in names:
            return int(values[names.index("RetransSegs")])
    return None


def tcp_connections(target_port: int, command_prefix: list[str] | None = None) -> dict[str, Any]:
    try:
        output = run_command([*(command_prefix or []), "ss", "-Htan"], timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError):
        return {"total": None, "target_port": target_port, "target_total": None, "states": {}}
    states: dict[str, int] = {}
    target_total = 0
    total = 0
    marker = f":{target_port}"
    for line in output.splitlines():
        fields = line.split()
        if not fields:
            continue
        total += 1
        states[fields[0]] = states.get(fields[0], 0) + 1
        if marker in line:
            target_total += 1
    return {"total": total, "target_port": target_port, "target_total": target_total, "states": states}


class HostSnapshot:
    def __init__(self) -> None:
        self.previous_cpu: tuple[int, int] | None = None
        self.previous_network: tuple[int, int] | None = None
        self.previous_disk: tuple[int, int] | None = None
        self.previous_retransmissions: int | None = None

    def capture(self) -> dict[str, Any]:
        total, idle = cpu_totals()
        cpu_percent = None
        if self.previous_cpu:
            total_delta = total - self.previous_cpu[0]
            idle_delta = idle - self.previous_cpu[1]
            if total_delta > 0:
                cpu_percent = 100.0 * (total_delta - idle_delta) / total_delta
        self.previous_cpu = (total, idle)
        memory = read_key_values("/proc/meminfo")
        received, transmitted = network_totals()
        network_delta = None
        if self.previous_network:
            network_delta = {
                "received_bytes": received - self.previous_network[0],
                "transmitted_bytes": transmitted - self.previous_network[1],
            }
        self.previous_network = (received, transmitted)
        disk_read, disk_written = disk_totals()
        disk_delta = None
        if self.previous_disk:
            disk_delta = {
                "read_bytes": disk_read - self.previous_disk[0],
                "written_bytes": disk_written - self.previous_disk[1],
            }
        self.previous_disk = (disk_read, disk_written)
        retransmissions = tcp_retransmissions()
        retransmissions_delta = None
        if retransmissions is not None and self.previous_retransmissions is not None:
            retransmissions_delta = retransmissions - self.previous_retransmissions
        self.previous_retransmissions = retransmissions
        load = os.getloadavg()
        return {
            "cpu_percent": cpu_percent,
            "load_1m": load[0],
            "load_5m": load[1],
            "load_15m": load[2],
            "memory_total_bytes": memory.get("MemTotal"),
            "memory_available_bytes": memory.get("MemAvailable"),
            "swap_total_bytes": memory.get("SwapTotal"),
            "swap_free_bytes": memory.get("SwapFree"),
            "network_delta": network_delta,
            "disk_delta": disk_delta,
            "tcp_retransmissions_delta": retransmissions_delta,
        }


def parse_mapping(values: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        label, separator, target = value.partition("=")
        if not separator or not label or not target:
            raise ValueError(f"expected LABEL=VALUE, got {value!r}")
        result[label] = target
    return result


def ssh_prefix(arguments: argparse.Namespace) -> list[str]:
    if not arguments.tdvm_ssh_target:
        return []
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={arguments.tdvm_known_hosts}",
        "-p", str(arguments.tdvm_ssh_port),
    ]
    if arguments.tdvm_ssh_identity:
        command.extend(["-i", arguments.tdvm_ssh_identity])
    command.append(arguments.tdvm_ssh_target)
    return command


def read_svid(command_prefix: list[str], container: str) -> dict[str, Any]:
    output = run_command([
        *command_prefix,
        "exec",
        container,
        "cat",
        "/run/argus-svid/status.json",
    ])
    return json.loads(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--target-port", type=int, default=1943)
    parser.add_argument("--host-container", action="append", default=[])
    parser.add_argument("--guest-container", action="append", default=[])
    parser.add_argument("--metrics-endpoint", action="append", default=[])
    parser.add_argument("--host-svid-container")
    parser.add_argument("--guest-svid-container")
    parser.add_argument("--tdvm-ssh-target")
    parser.add_argument("--tdvm-ssh-port", type=int, default=2222)
    parser.add_argument("--tdvm-ssh-identity", default="")
    parser.add_argument("--tdvm-known-hosts", default="/tmp/argus-benchmark-known-hosts")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.interval_seconds <= 0:
        raise SystemExit("--interval-seconds must be positive")
    output_path = Path(arguments.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path = Path(arguments.stop_file)
    host_containers = parse_mapping(arguments.host_container)
    guest_containers = parse_mapping(arguments.guest_container)
    metrics_endpoints = parse_mapping(arguments.metrics_endpoint)
    ssh = ssh_prefix(arguments)
    guest_docker = [*ssh, "sudo", "-n", "/usr/local/bin/docker"] if ssh else []
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    host_snapshot = HostSnapshot()

    with output_path.open("a", encoding="utf-8", buffering=1) as destination:
        while not stopping and not stop_path.exists():
            started = time.monotonic()
            errors: list[str] = []
            record: dict[str, Any] = {
                "schema_version": "argus-benchmark-resource-v1",
                "type": "resource",
                "timestamp_unix_ms": int(time.time() * 1000),
            }
            try:
                record["host"] = host_snapshot.capture()
            except (OSError, ValueError) as error:
                errors.append(f"host:{error}")
            record["connections"] = tcp_connections(arguments.target_port)
            if ssh:
                record["guest_connections"] = tcp_connections(arguments.target_port, ssh)
            try:
                record["host_containers"] = docker_stats(["docker"], host_containers)
            except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
                errors.append(f"host_containers:{error}")
            if guest_containers:
                try:
                    record["guest_containers"] = docker_stats(guest_docker, guest_containers)
                except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
                    errors.append(f"guest_containers:{error}")
            metrics: dict[str, Any] = {}
            for label, url in metrics_endpoints.items():
                try:
                    metrics[label] = fetch_prometheus(url)
                except (OSError, ValueError) as error:
                    errors.append(f"metrics[{label}]:{error}")
            record["metrics"] = metrics
            svids: dict[str, Any] = {}
            if arguments.host_svid_container:
                try:
                    svids["openclaw"] = read_svid(["docker"], arguments.host_svid_container)
                except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
                    errors.append(f"svid[openclaw]:{error}")
            if arguments.guest_svid_container:
                try:
                    svids["openviking"] = read_svid(guest_docker, arguments.guest_svid_container)
                except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
                    errors.append(f"svid[openviking]:{error}")
            record["svids"] = svids
            record["errors"] = errors
            destination.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            remaining = arguments.interval_seconds - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
