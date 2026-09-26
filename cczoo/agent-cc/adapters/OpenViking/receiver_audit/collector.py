"""Independent nonblocking receiver telemetry with interval coverage.

Kernel credentials attribute observations. Experiment membership does not grant
Argus admission. There are no data-plane ACKs or application control callbacks.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time
import uuid

from .middleware import ID, MAX_FRAME


def clocks():
    return {"at_ms": time.time_ns() // 1000000, "monotonic_ns": time.monotonic_ns()}


def protected_file(path):
    path = Path(path)
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022:
        raise ValueError("audit binding must be a root-owned regular file without group/world write")
    for parent in path.resolve().parents:
        info = parent.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("audit binding parents must be root-owned without group/world write")
    return path


def process_facts(pid):
    root = Path("/proc") / str(pid)
    fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": int(pid), "start_time": fields[19], "uid": root.stat().st_uid,
            "pid_namespace": os.readlink(str(root / "ns/pid")),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "cgroup_sha256": hashlib.sha256((root / "cgroup").read_bytes()).hexdigest()}


def make_binding(target_path, run_id):
    if not ID.fullmatch(run_id):
        raise ValueError("invalid experiment run ID")
    raw = protected_file(target_path).read_bytes()
    target = json.loads(raw)
    facts = process_facts(int(target["pid"]))
    if any(str(target.get(k)) != str(facts[k]) for k in ("start_time", "boot_id", "pid_namespace")):
        raise ValueError("target process start time, boot or namespace changed")
    container = target["container_id"]
    if not re.fullmatch(r"[0-9a-f]{64}", container):
        raise ValueError("full Docker container ID required")
    if container not in (Path("/proc") / str(facts["pid"]) / "cgroup").read_text():
        raise ValueError("target process cgroup does not identify the experimental container")
    return {"schema_version": 2, "run_id": run_id, "instance_id": container,
            "launch_id": target["launch_id"], "target_sha256": hashlib.sha256(raw).hexdigest(), "process": facts}


def validate_binding(binding):
    # v1 binding files remain usable; previous v1 JSONL uses the v1 assessor.
    if binding.get("schema_version") not in (1, 2) or not ID.fullmatch(binding.get("run_id", "")):
        raise ValueError("invalid receiver binding")
    if not re.fullmatch("[0-9a-f]{64}", binding.get("instance_id", "")) or not binding.get("launch_id"):
        raise ValueError("receiver binding lacks container/launch provenance")
    if not re.fullmatch("[0-9a-f]{64}", binding.get("target_sha256", "")):
        raise ValueError("receiver binding lacks deployment provenance")


def resolve_peer(pid, uid, bindings):
    facts = process_facts(pid)
    if facts["uid"] != uid:
        return None
    cgroup = (Path("/proc") / str(pid) / "cgroup").read_text()
    for binding in bindings.values():
        if facts == binding["process"] or binding["instance_id"] in cgroup:
            return dict(instance_id=binding["instance_id"], launch_id=binding["launch_id"], process=facts)
    return None


class Collector:
    def __init__(self, binding, output, *, peer_resolver=resolve_peer):
        validate_binding(binding)
        self.binding, self.peer_resolver = binding, peer_resolver
        self.bindings = {binding["instance_id"]: binding}
        self.output = open(os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8")
        self.collector_id = uuid.uuid4().hex
        self.sequence = 0
        self.sources, self.first_reads = {}, {}
        self.finalized = False
        self.socket = None
        self.write("receiver_start", boundary="asgi_application_read", binding=binding,
                   protocol="nonblocking_datagram", batch_interval_ms=250)
        self.flush()

    def write(self, kind, **fields):
        self.sequence += 1
        row = dict(type=kind, schema_version=2, collector_id=self.collector_id,
                   record_seq=self.sequence, run_id=self.binding["run_id"], **clocks())
        row.update(fields)
        self.output.write(json.dumps(row, separators=(",", ":")) + "\n")
        return row

    def flush(self):
        self.output.flush()
        os.fsync(self.output.fileno())

    def add_target(self, binding):
        validate_binding(binding)
        if binding["run_id"] != self.binding["run_id"]:
            raise ValueError("observation target belongs to another run")
        prior = self.bindings.get(binding["instance_id"])
        if prior and prior["launch_id"] != binding["launch_id"]:
            raise ValueError("conflicting launch association")
        self.bindings[binding["instance_id"]] = binding
        self.write("target_added", binding=binding, reason="experiment_observation_only")
        self.flush()

    def _interval(self, source, end, status, reason):
        start = source["watermark"] or source["started"]
        if end["monotonic_ns"] <= start["monotonic_ns"]:
            return
        self.write("coverage_interval", **source["identity"], status=status, reason=reason,
                   started_at_ms=start["at_ms"], ended_at_ms=end["at_ms"],
                   started_monotonic_ns=start["monotonic_ns"], ended_monotonic_ns=end["monotonic_ns"])

    def accept(self, raw, pid, uid):
        if self.finalized:
            return
        try:
            frame = json.loads(raw)
            if (not isinstance(frame, dict) or len(raw) > MAX_FRAME or frame.get("schema_version") != 2 or
                    frame.get("run_id") != self.binding["run_id"] or
                    not ID.fullmatch(frame.get("source_id", "")) or
                    any(type(frame.get(k)) is not int or frame[k] < 0 for k in
                        ("source_seq", "dropped", "at_ms", "monotonic_ns", "source_started_at_ms", "source_started_monotonic_ns"))):
                raise ValueError("invalid metadata frame")
            peer = self.peer_resolver(pid, uid, self.bindings)
            if peer is None:
                self.write("receiver_gap", reason="unbound_process", pid=pid)
                return
            identity = dict(peer, source_id=frame["source_id"], provenance="kernel_process_and_deployment")
            key = (pid, peer["process"]["start_time"], frame["source_id"])
            source = self.sources.get(key)
            if source is None:
                source = self.sources[key] = dict(identity=identity, seq=0, drops=0, dirty=False,
                    watermark=None, stopped=False, started={"at_ms": frame["source_started_at_ms"],
                    "monotonic_ns": frame["source_started_monotonic_ns"]}, last_mono=0)
                self.write("source_seen", **identity)
            source["dirty"] |= (frame["source_seq"] != source["seq"] + 1 or
                                frame["dropped"] != source["drops"] or frame["monotonic_ns"] < source["last_mono"])
            source.update(seq=frame["source_seq"], drops=frame["dropped"], last_mono=frame["monotonic_ns"])
            when = {k: frame[k] for k in ("at_ms", "monotonic_ns")}
            op = frame.get("op")
            if op == "watermark":
                clean = source["watermark"] is not None and not source["dirty"]
                self._interval(source, when, "COMPLETE" if clean else "UNKNOWN",
                               "contiguous_source_watermarks" if clean else "initial_or_lost_telemetry")
                source.update(watermark=when, dirty=False)
                self.write("source_watermark", **identity, **when, source_seq=frame["source_seq"], dropped=frame["dropped"])
                return
            if op == "source_stop":
                self._interval(source, when, "UNKNOWN", "source_shutdown_tail")
                source["stopped"] = True
                self.write("source_stop", **identity, **when)
                return
            if op not in ("begin", "pending", "observed", "end"):
                raise ValueError("unknown event")
            if not ID.fullmatch(frame.get("request_id", "")) or not ID.fullmatch(frame.get("stream_id", "")):
                raise ValueError("invalid correlation metadata")
            fields = dict(identity, **when, source_seq=frame["source_seq"], request_id=frame["request_id"],
                          stream_id=frame["stream_id"], received_at_ms=time.time_ns() // 1000000,
                          received_monotonic_ns=time.monotonic_ns())
            if op == "begin":
                self.write("received", **fields, phase="request_enter", chunk_seq=0,
                           received_body_bytes=0, correlated=frame.get("correlated") is True)
            elif op in ("pending", "observed"):
                if type(frame.get("chunk_seq")) is not int or frame["chunk_seq"] < 1:
                    raise ValueError("invalid read sequence")
                if op == "pending":
                    self.write("receive_pending", **fields, chunk_seq=frame["chunk_seq"])
                else:
                    if (frame.get("message_type") not in ("http.request", "http.disconnect") or
                            type(frame.get("body_bytes")) is not int or frame["body_bytes"] < 0 or
                            type(frame.get("more_body")) is not bool):
                        raise ValueError("invalid read observation")
                    row = self.write("received", **fields, phase="body_read", chunk_seq=frame["chunk_seq"],
                                     received_body_bytes=frame["body_bytes"], message_type=frame["message_type"], more_body=frame["more_body"])
                    if frame["body_bytes"] and frame["request_id"] not in self.first_reads and len(self.first_reads) < 4096:
                        self.first_reads[frame["request_id"]] = row
            else:
                self.write("request_end", **fields)
        except (ValueError, KeyError, TypeError, OSError):
            self.write("receiver_gap", reason="invalid_or_unattributed_datagram", pid=pid)
            for source in self.sources.values():
                if source["identity"]["process"]["pid"] == pid:
                    source["dirty"] = True

    def listen(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.socket.setblocking(False)
        self.socket.bind(str(path))
        asyncio.get_running_loop().add_reader(self.socket.fileno(), self.drain)

    def drain(self):
        for _ in range(256):
            try:
                raw, ancillary, flags, _ = self.socket.recvmsg(MAX_FRAME + 1, socket.CMSG_SPACE(12))
            except BlockingIOError:
                break
            credentials = [data for level, kind, data in ancillary if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
            if flags & socket.MSG_TRUNC or len(credentials) != 1:
                self.write("receiver_gap", reason="missing_kernel_credentials_or_truncation")
                for source in self.sources.values():
                    source["dirty"] = True
                continue
            pid, uid, _ = struct.unpack("3i", credentials[0][:12])
            self.accept(raw, pid, uid)

    def status(self, request_id):
        self.flush()
        return {"schema_version": 2, "run_id": self.binding["run_id"], "collector_id": self.collector_id,
                "first_read": self.first_reads.get(request_id)}

    def finish(self):
        if self.finalized:
            raise ValueError("collector already finalized")
        if self.socket:
            self.drain()
        end = clocks()
        for source in self.sources.values():
            if not source["stopped"]:
                self._interval(source, end, "UNKNOWN", "uncovered_final_or_crash_tail")
        self.write("receiver_stop", complete=False, coverage="intervals_only", sources=len(self.sources))
        self.flush()
        self.finalized = True
        self.output.close()
        if self.socket:
            asyncio.get_running_loop().remove_reader(self.socket.fileno())
            self.socket.close()
        return {"schema_version": 2, "finalized": True, "coverage": "intervals_only"}


async def serve(args):
    if os.geteuid() != 0:
        raise ValueError("collector must run as root in the service host PID namespace")
    binding = json.loads(protected_file(args.binding).read_text())
    for path in (Path(args.socket), Path(args.control)):
        if not path.is_absolute() or path.exists() or path.is_symlink():
            raise ValueError("new absolute socket paths required")
        parent = path.parent.stat()
        if parent.st_uid != 0 or parent.st_mode & 0o022:
            raise ValueError("socket directories must be root-owned without group/world write")
    if Path(args.socket).parent.resolve() in Path(args.control).resolve().parents:
        raise ValueError("control socket must be outside the mounted data directory")
    collector = Collector(binding, args.output)
    stop = asyncio.Event()
    async def control(reader, writer):
        _, uid, _ = struct.unpack("3i", writer.get_extra_info("socket").getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        try:
            frame = json.loads(await asyncio.wait_for(reader.readline(), 2))
            if uid != 0:
                raise ValueError("unauthorized control operation")
            if frame.get("op") == "finalize":
                result = collector.finish()
                stop.set()
            elif frame.get("op") == "status":
                result = collector.status(frame.get("request_id", ""))
            elif frame.get("op") == "add-target":
                collector.add_target(make_binding(frame["target"], frame["run_id"]))
                result = {"registered": True, "purpose": "observation_only"}
            else:
                raise ValueError("unknown control operation")
            writer.write((json.dumps(result) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
    collector.listen(args.socket)
    os.chown(args.socket, 0, args.socket_gid)
    os.chmod(args.socket, 0o660)
    server = await asyncio.start_unix_server(control, args.control, limit=MAX_FRAME)
    os.chmod(args.control, 0o600)
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), .25)
            except asyncio.TimeoutError:
                collector.flush()
    finally:
        server.close()
        await server.wait_closed()
        if not collector.finalized:
            collector.output.close()
            if collector.socket:
                asyncio.get_running_loop().remove_reader(collector.socket.fileno())
                collector.socket.close()
        for path in (args.socket, args.control):
            Path(path).unlink(missing_ok=True)


async def command(path, value):
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path), 2)
    try:
        writer.write((json.dumps(value) + "\n").encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), 3)
        if not raw:
            raise RuntimeError("collector unavailable; uncovered interval remains UNKNOWN")
        return json.loads(raw)
    finally:
        writer.close()
        await writer.wait_closed()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    b = commands.add_parser("bind")
    for option in ("target", "run-id", "output"):
        b.add_argument("--" + option, required=True)
    s = commands.add_parser("serve")
    for option in ("socket", "control", "binding", "output"):
        s.add_argument("--" + option, required=True)
    s.add_argument("--socket-gid", type=int, default=0)
    f = commands.add_parser("finalize"); f.add_argument("--control", required=True)
    q = commands.add_parser("status"); q.add_argument("--control", required=True); q.add_argument("--request-id", required=True)
    a = commands.add_parser("add-target"); a.add_argument("--control", required=True)
    a.add_argument("--target", required=True); a.add_argument("--run-id", required=True)
    args = p.parse_args()
    if args.command == "bind":
        value = make_binding(args.target, args.run_id)
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            json.dump(value, output); output.flush(); os.fsync(output.fileno())
        print(json.dumps(value))
    elif args.command == "serve":
        asyncio.run(serve(args))
    else:
        value = {"op": args.command}
        if args.command == "status": value["request_id"] = args.request_id
        if args.command == "add-target": value.update(target=args.target, run_id=args.run_id)
        print(json.dumps(asyncio.run(command(args.control, value))))


if __name__ == "__main__":
    main()
