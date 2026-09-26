#!/usr/bin/env python3
"""Read-only E3 snapshots, independent Docker create stream, and narrow checks.

This tool never renews identities, repeats a launch, deletes Agent state or
creates a container. Remote freshness/Quote measurements remain separate.
"""
import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import sys
import time
from urllib.parse import urlencode

from common import append, atomic, digest, read, require


def now_ms():
    return time.time_ns() // 1000000


def runtime(config_file):
    from trusted_runtime import load
    workload, _, c = load(config_file)
    return workload, c


def capture(config_file):
    workload, c = runtime(config_file)
    d = workload.Deployment(c)
    result = {"schema": "argus.lifecycle-snapshot.v1", "started_at_ms": now_ms(),
              "config_sha256": digest(c), "workload_id": d.workload["id"],
              "target_id": d.identity["target_id"], "hardware_acceptance": "NOT_RUN"}
    first = workload.status(c)
    result["status"] = first
    if d.target.exists():
        result["target"] = json.loads(workload.run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
    state_path = d.records / "launch-state.json"
    if state_path.exists():
        state = json.loads(workload.protected_file(state_path).read_text())
        result["launch"] = {key: state.get(key) for key in ("run_id", "launch_id", "container_id", "stage", "config_sha256")}
    ids = workload.run(["docker", "ps", "-aq", "--no-trunc", "--filter", "label=io.trucon.workload-id=" + d.workload["id"]]).splitlines()
    require(all(re.fullmatch("[0-9a-f]{64}", value) for value in ids), "Docker inventory contains noncanonical container IDs")
    containers = json.loads(workload.run(["docker", "inspect", *ids])) if ids else []
    result["containers"] = [{"container_id": entry["Id"],
                             "launch_id": entry.get("Config", {}).get("Labels", {}).get("io.trucon.launch-id"),
                             "running": entry.get("State", {}).get("Running", False)} for entry in containers]
    if first["ready"]:
        cert, bundle = d.credentials / "current/svid.pem", d.credentials / "current/bundle.pem"
        before_bytes = cert.read_bytes()
        public = ssl._ssl._test_decode_cert(str(cert))
        validated = subprocess.run(["openssl", "verify", "-CAfile", str(bundle), "-untrusted", str(cert), str(cert)],
                                   capture_output=True, timeout=10, check=False)
        serial = str(int(public["serialNumber"], 16))
        uris = [value for kind, value in public.get("subjectAltName", ()) if kind == "URI"]
        require(validated.returncode == 0 and uris == [d.identity["target_id"]] and
                serial == first["target_serial"] and ssl.cert_time_to_seconds(public["notAfter"]) > time.time(),
                "public workload certificate does not match valid readiness")
        require(cert.read_bytes() == before_bytes and workload.status(c) == first,
                "readiness or certificate changed during snapshot; collect a new snapshot")
        result["credential"] = {"serial": serial, "expires_at": public["notAfter"], "uri_san": uris,
                                "certificate_sha256": hashlib.sha256(before_bytes).hexdigest(), "chain_valid": True}
    result["completed_at_ms"] = now_ms()
    return result


class DockerConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=5):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def observe_creates(socket_path, workload_id, duration, output):
    require(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", workload_id), "invalid workload ID")
    require(1 <= duration <= 3600, "create observation duration must be in [1,3600]")
    path = Path(output)
    # Reserve without overwrite before opening any socket; no event body or environment is saved.
    os.close(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    until = math.ceil(time.time()) + duration
    filters = {"type": ["container"], "event": ["create"], "label": ["io.trucon.workload-id=" + workload_id]}
    conn = DockerConnection(socket_path, timeout=duration + 10)
    seq, complete = 0, False
    def emit(kind, **fields):
        nonlocal seq
        seq += 1
        append(path, {"schema": "argus.docker-creates.v1", "type": kind, "record_seq": seq,
                      "workload_id": workload_id, "at_ms": now_ms(), **fields})
    try:
        conn.request("GET", "/events?" + urlencode({"until": str(until), "filters": json.dumps(filters)}))
        response = conn.getresponse()
        require(response.status == 200, "Docker event subscription failed")
        peer = struct.unpack("3i", conn.sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(peer[1] == 0, "Docker create observer requires a root-owned host daemon socket")
        emit("create_observer_start", until_ms=until * 1000, daemon_peer_pid=peer[0], daemon_peer_uid=peer[1])
        print(json.dumps({"status": "CREATE_OBSERVER_READY", "workload_id": workload_id, "until_ms": until * 1000}), flush=True)
        while True:
            raw = response.readline(65537)
            if not raw:
                # The daemon's server-side `until` closes the completed stream.
                # Early EOF/socket timeout/process loss is never complete coverage.
                complete = now_ms() >= until * 1000
                break
            require(len(raw) <= 65536 and raw.endswith(b"\n"), "Docker event frame truncated or oversized")
            event = json.loads(raw)
            attrs = event.get("Actor", {}).get("Attributes", {})
            cid = event.get("Actor", {}).get("ID") or event.get("id")
            require(event.get("Type") == "container" and event.get("Action") == "create" and
                    attrs.get("io.trucon.workload-id") == workload_id and
                    isinstance(cid, str) and re.fullmatch("[0-9a-f]{64}", cid), "Docker event differs from requested stream")
            stamp = event.get("timeNano")
            require(type(stamp) is int and stamp > 0, "Docker event lacks creation timestamp")
            emit("container_create", container_id=cid, launch_id=attrs.get("io.trucon.launch-id"), created_at_ns=stamp)
    except Exception as error:
        complete = False
        emit("create_observer_error", error_class=type(error).__name__)
    finally:
        conn.close()
        emit("create_observer_stop", complete=complete, coverage_until_ms=until * 1000 if complete else None)
    return complete


def rotation(before, after):
    result = {"schema": "argus.lifecycle-result.v1", "case": "workload_svid_rotation", "result": "UNKNOWN",
              "node_quote_reuse": "NOT_RUN", "business_continuity": "NOT_RUN"}
    if not same_run(before, after):
        return result | {"reason": "snapshots differ in configuration, workload, target identity or time order"}
    if not before.get("status", {}).get("ready") or not after.get("status", {}).get("ready"):
        return result | {"reason": "both snapshots need valid current readiness"}
    if not before.get("target") or before["target"] != after.get("target") or not before["status"].get("helper_invocation_id") or before["status"]["helper_invocation_id"] != after["status"].get("helper_invocation_id"):
        return result | {"reason": "target or Helper changed; not a within-instance rotation observation"}
    a, b = before.get("credential", {}), after.get("credential", {})
    if not all(x.get("chain_valid") is True and x.get("uri_san") == [before["target_id"]] and
               x.get("serial") == snap["status"].get("target_serial") for x, snap in ((a, before), (b, after))):
        return result | {"reason": "validated certificate/readiness association missing"}
    if a.get("serial") == b.get("serial") or a.get("certificate_sha256") == b.get("certificate_sha256"):
        return result | {"reason": "no new public SVID observed"}
    return result | {"result": "PASS", "before_serial": a["serial"], "after_serial": b["serial"],
                     "scope": "same instance and Helper, valid public Workload SVID changed"}


def same_run(before, after):
    return (before.get("schema") == after.get("schema") == "argus.lifecycle-snapshot.v1" and
            all(before.get(key) and before.get(key) == after.get(key) for key in ("config_sha256", "workload_id", "target_id")) and
            type(before.get("completed_at_ms")) is int and type(after.get("started_at_ms")) is int and
            before["completed_at_ms"] <= after["started_at_ms"])


def resumed_launch(before, after, resumed, creates):
    result = {"schema": "argus.lifecycle-result.v1", "case": "resume_launch", "result": "UNKNOWN",
              "same_operation": "UNKNOWN", "duplicate_creation": "UNKNOWN", "hardware_acceptance": "NOT_RUN"}
    if not same_run(before, after):
        return result | {"reason": "snapshot association or time order differs"}
    a, b = before.get("launch", {}), after.get("launch", {})
    if not a.get("launch_id") or a.get("stage") in ("submission_unknown", "complete"):
        return result | {"reason": "before snapshot must retain a known unfinished launch"}
    if not (a["launch_id"] == b.get("launch_id") == resumed.get("launch_id") and
            b.get("stage") == "complete" and resumed.get("resumed") is True and
            a.get("config_sha256") == b.get("config_sha256") == resumed.get("config_sha256") == before["config_sha256"] and
            b.get("container_id") == resumed.get("container_id") and b.get("container_id")):
        return result | {"result": "FAIL", "same_operation": "FAIL", "reason": "resume result does not preserve the recorded operation"}
    result["same_operation"] = "PASS"
    logs = creates or []
    if (not logs or any(row.get("schema") != "argus.docker-creates.v1" or row.get("workload_id") != before["workload_id"] for row in logs) or
            [row.get("record_seq") for row in logs] != list(range(1, len(logs) + 1)) or
            logs[0].get("type") != "create_observer_start" or logs[-1].get("type") != "create_observer_stop" or
            logs[-1].get("complete") is not True or any(row.get("type") == "create_observer_error" for row in logs) or
            logs[0].get("at_ms", float("inf")) > before.get("started_at_ms", 0) or
            logs[-1].get("coverage_until_ms", 0) < after["completed_at_ms"]):
        return result | {"reason": "independent live Docker create stream lacks complete snapshot-window coverage"}
    if (logs[0].get("daemon_peer_uid") != 0 or type(logs[0].get("daemon_peer_pid")) is not int or
            logs[0].get("until_ms") != logs[-1].get("coverage_until_ms") or
            any(type(row.get("at_ms")) is not int for row in logs) or
            any(a["at_ms"] > b["at_ms"] for a, b in zip(logs, logs[1:])) or
            logs[-1]["at_ms"] < logs[-1]["coverage_until_ms"]):
        return result | {"reason": "Docker stream source/time metadata inconsistent"}
    if any(row.get("type") != "container_create" or type(row.get("created_at_ns")) is not int or
           row["created_at_ns"] <= 0 or not isinstance(row.get("container_id"), str) or
           not re.fullmatch("[0-9a-f]{64}", row["container_id"]) for row in logs[1:-1]):
        return result | {"reason": "Docker stream contains malformed or unrecognized events"}
    before_ids = {r["container_id"] for r in before.get("containers", []) if r.get("launch_id") == a["launch_id"]}
    after_ids = {r["container_id"] for r in after.get("containers", []) if r.get("launch_id") == a["launch_id"]}
    observed = [row for row in logs if row.get("type") == "container_create" and
                row.get("created_at_ns", 0) >= before["started_at_ms"] * 1000000 and
                row.get("created_at_ns", 0) <= after["completed_at_ms"] * 1000000]
    ids = [row.get("container_id") for row in observed]
    if any(row.get("launch_id") != a["launch_id"] for row in observed) or len(ids) != len(set(ids)) or (before_ids | after_ids | set(ids)) != {b["container_id"]} or after_ids != {b["container_id"]}:
        return result | {"result": "FAIL", "duplicate_creation": "FAIL", "reason": "extra or mismatched container creation during the exclusive resume experiment"}
    return result | {"result": "PASS", "duplicate_creation": "PASS", "launch_id": a["launch_id"],
                     "container_id": b["container_id"], "observed_create_events": len(ids),
                     "scope": "no second Docker create in the observed exclusive recovery window"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("snapshot")
    c.add_argument("--config", required=True)
    c.add_argument("--output", required=True)
    c = sub.add_parser("observe-creates")
    c.add_argument("--docker-socket", default="/var/run/docker.sock")
    c.add_argument("--workload-id", required=True)
    c.add_argument("--duration", type=int, default=120)
    c.add_argument("--output", required=True)
    for name in ("rotation", "resume"):
        c = sub.add_parser(name)
        for field in ("before", "after", "output"):
            c.add_argument("--" + field, required=True)
        if name == "resume":
            c.add_argument("--resume-result", required=True)
            c.add_argument("--creates")
    args = parser.parse_args()
    if args.command == "observe-creates":
        raise SystemExit(0 if observe_creates(args.docker_socket, args.workload_id, args.duration, args.output) else 2)
    if args.command == "snapshot":
        atomic(args.output, capture(args.config))
        return
    before, after = read(args.before), read(args.after)
    if args.command == "rotation":
        result = rotation(before, after)
    else:
        creates = None
        if args.creates:
            try:
                creates = [json.loads(line) for line in Path(args.creates).read_text().splitlines() if line.strip()]
            except (OSError, ValueError):
                creates = None
        result = resumed_launch(before, after, read(args.resume_result), creates)
    atomic(args.output, result)
    print(json.dumps(result))
    raise SystemExit(0 if result["result"] == "PASS" else 1 if result["result"] == "FAIL" else 2)


if __name__ == "__main__":
    main()
