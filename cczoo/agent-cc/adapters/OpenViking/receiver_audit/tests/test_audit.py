"""Real Linux datagrams/credentials and ASGI reads; no TDX/model claims."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from receiver_audit.collector import Collector, command, process_facts, resolve_peer, serve
from receiver_audit.middleware import ReceiverAudit, Telemetry
from receiver_audit.startup import wrap_uvicorn, verify_upstream


def binding(pid=None, container="a", launch="launch-1"):
    return {"schema_version": 1, "run_id": "run-1", "instance_id": container * 64,
            "launch_id": launch, "target_sha256": "b" * 64, "process": process_facts(pid or os.getpid())}


def scope(request_id="request-1"):
    return {"type": "http", "headers": [(b"x-argus-run-id", b"run-1"),
            (b"x-argus-request-id", request_id.encode()), (b"authorization", b"SECRET-key")],
            "path": "/SECRET-path", "query_string": b"SECRET-query"}


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux SCM_CREDENTIALS and /proc required")
class DatagramAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="argus-audit-")
        self.path = Path(self.temp.name)
        self.collector = Collector(binding(), self.path / "receiver.jsonl")
        self.sock = str(self.path / "audit.sock")
        self.collector.listen(self.sock)
        self.channels = []

    async def asyncTearDown(self):
        for channel in self.channels:
            await channel.close()
        await asyncio.sleep(.02)
        if not self.collector.finalized:
            self.collector.finish()
        self.temp.cleanup()

    def rows(self):
        if not self.collector.finalized:
            self.collector.flush()
        return [json.loads(line) for line in (self.path / "receiver.jsonl").read_text().splitlines()]

    def telemetry(self, sock=None):
        channel = Telemetry(sock or self.sock, "run-1", interval=.02)
        self.channels.append(channel)
        return channel

    async def request(self, messages, *, channel=None, request_id="request-1", scope_value=None):
        delivered = []
        async def receive():
            return messages.pop(0)
        async def app(_, wrapped_receive, __):
            while messages:
                delivered.append(await wrapped_receive())
        channel = channel or self.telemetry()
        await ReceiverAudit(app, self.sock, "run-1", telemetry=channel)(scope_value or scope(request_id), receive, None)
        await asyncio.sleep(.05)
        return delivered

    async def test_chunks_return_without_collector_ack_and_metadata_only(self):
        chunks = [{"type": "http.request", "body": b"SECRET-body", "more_body": True},
                  {"type": "http.request", "body": b"second", "more_body": False}]
        expected = list(chunks)
        self.assertEqual(await self.request(chunks), expected)
        rows = self.rows()
        reads = [row for row in rows if row.get("phase") == "body_read"]
        self.assertEqual([r["received_body_bytes"] for r in reads], [11, 6])
        self.assertEqual([r["chunk_seq"] for r in reads], [1, 2])
        self.assertTrue(all(r["process"]["pid"] == os.getpid() for r in reads))
        self.assertTrue(all(r["provenance"] == "kernel_process_and_deployment" for r in reads))
        self.assertNotIn("SECRET", (self.path / "receiver.jsonl").read_text())

    async def test_missing_collector_does_not_block_receive_or_answer(self):
        channel = self.telemetry(str(self.path / "missing.sock"))
        self.assertEqual(len(await self.request([{"type": "http.request", "body": b"fact"}], channel=channel)), 1)
        self.assertGreater(channel.dropped, 0)

    async def test_disconnect_does_not_invalidate_later_complete_intervals(self):
        channel = self.telemetry()
        await self.request([{"type": "http.disconnect"}], channel=channel)
        channel.emit("watermark"); self.collector.drain()
        channel.emit("watermark"); self.collector.drain()
        rows = self.rows()
        self.assertTrue(any(r.get("status") == "COMPLETE" for r in rows))
        self.assertFalse(any(r.get("reason") == "incomplete_request" for r in rows))

    async def test_malformed_and_duplicate_correlation_headers_preserve_business(self):
        item = scope()
        item["headers"].append((b"x-argus-request-id", b"duplicate"))
        self.assertEqual(len(await self.request([{"type": "http.request", "body": b"x"}], scope_value=item)), 1)
        received = next(r for r in self.rows() if r.get("phase") == "request_enter")
        self.assertFalse(received["correlated"])
        self.assertNotEqual(received["request_id"], "request-1")

    async def test_drop_marks_only_affected_interval_then_recovers(self):
        channel = self.telemetry()
        channel.emit("watermark"); self.collector.drain()
        channel.emit("watermark"); self.collector.drain()
        channel.socket_path = str(self.path / "missing.sock")
        channel.emit("pending", request_id="request-1", stream_id="s", chunk_seq=1)
        channel.socket_path = self.sock
        channel.emit("watermark"); self.collector.drain()
        channel.emit("watermark"); self.collector.drain()
        intervals = [r for r in self.rows() if r["type"] == "coverage_interval"]
        statuses = [r["status"] for r in intervals]
        self.assertIn("UNKNOWN", statuses[2:])
        self.assertEqual(statuses[-1], "COMPLETE")

    async def test_peer_supplied_pid_cannot_override_kernel_credentials(self):
        code = """import socket,json,sys,time
s=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
s.sendto(json.dumps({'schema_version':2,'run_id':'run-1','source_id':'child','source_seq':1,'dropped':0,'at_ms':1,'monotonic_ns':1,'source_started_at_ms':1,'source_started_monotonic_ns':1,'op':'watermark','pid':int(sys.argv[2])}).encode(),sys.argv[1])
time.sleep(.1)
"""
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", code, self.sock, str(os.getpid()))
        await child.wait()
        self.assertTrue(any(r.get("reason") == "unbound_process" for r in self.rows()))
        self.assertFalse(any(r["type"] == "source_seen" for r in self.rows()))

    async def test_explicit_replacement_is_observed_without_argus_registration(self):
        code = """import os,sys,asyncio
from receiver_audit.middleware import Telemetry
print(os.getpid(),flush=True);sys.stdin.readline()
async def run():
 t=Telemetry(sys.argv[1],'run-1');t.start()
 t.emit('begin',request_id='replacement',stream_id='s',correlated=True)
 t.emit('pending',request_id='replacement',stream_id='s',chunk_seq=1)
 t.emit('observed',request_id='replacement',stream_id='s',chunk_seq=1,message_type='http.request',body_bytes=7,more_body=False)
 await asyncio.sleep(.1);await t.close();await asyncio.sleep(.1)
asyncio.run(run())
"""
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", code, self.sock,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=dict(os.environ, PYTHONPATH=str(ROOT)))
        pid = int(await child.stdout.readline())
        self.collector.add_target(binding(pid, "c", "replacement-launch"))
        child.stdin.write(b"go\n"); await child.stdin.drain()
        self.assertEqual(await child.wait(), 0)
        rows = self.rows()
        received = next(r for r in rows if r.get("phase") == "body_read")
        self.assertEqual(received["process"]["pid"], pid)
        self.assertEqual(received["instance_id"], "c" * 64)
        self.assertTrue(any(r["type"] == "target_added" for r in rows))

    async def test_same_container_successor_uses_actual_kernel_process(self):
        # Resolver fixture substitutes only the cgroup filesystem text, never
        # the kernel credentials or actual PID/starttime returned by /proc.
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time;time.sleep(.3)")
        original = Path.read_text
        def cgroup(path, *args, **kwargs):
            if str(path) == f"/proc/{child.pid}/cgroup":
                return "0::/docker/" + "a" * 64
            return original(path, *args, **kwargs)
        try:
            with patch.object(Path, "read_text", cgroup):
                result = resolve_peer(child.pid, os.getuid(), self.collector.bindings)
            self.assertEqual(result["instance_id"], "a" * 64)
            self.assertEqual(result["process"]["pid"], child.pid)
            self.assertNotEqual(result["process"]["start_time"], None)
        finally:
            await child.wait()

    async def test_source_crash_keeps_complete_history_and_unknown_tail(self):
        code = """import os,sys,asyncio
from receiver_audit.middleware import Telemetry
print(os.getpid(),flush=True);sys.stdin.readline()
async def run():
 t=Telemetry(sys.argv[1],'run-1',interval=.02);t.start();await asyncio.sleep(.07)
 t.emit('pending',request_id='r',stream_id='s',chunk_seq=1)
 await asyncio.sleep(.01);os._exit(9)
asyncio.run(run())
"""
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", code, self.sock,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=dict(os.environ, PYTHONPATH=str(ROOT)))
        pid = int(await child.stdout.readline())
        self.collector.add_target(binding(pid, "c", "crash-launch"))
        child.stdin.write(b"go\n"); await child.stdin.drain()
        self.assertEqual(await child.wait(), 9)
        self.collector.finish()
        rows = self.rows()
        self.assertTrue(any(r.get("status") == "COMPLETE" for r in rows))
        self.assertTrue(any(r.get("reason") == "uncovered_final_or_crash_tail" for r in rows))

    async def test_collector_process_crash_does_not_block_read_delivery(self):
        sock, trace = str(self.path / "child.sock"), str(self.path / "child.jsonl")
        code = """import asyncio,json,sys
from receiver_audit.collector import Collector
async def run():
 c=Collector(json.loads(sys.argv[1]),sys.argv[3]);c.listen(sys.argv[2]);print('READY',flush=True)
 await asyncio.Event().wait()
asyncio.run(run())
"""
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", code, json.dumps(binding()), sock, trace,
            env=dict(os.environ, PYTHONPATH=str(ROOT)), stdout=subprocess.PIPE)
        try:
            self.assertEqual(await child.stdout.readline(), b"READY\n")
            channel = self.telemetry(sock)
            channel.start(); await asyncio.sleep(.03)
            child.kill(); await child.wait()
            delivered = await self.request([{"type": "http.request", "body": b"continued"}], channel=channel)
            self.assertEqual(delivered[0]["body"], b"continued")
            self.assertGreater(channel.dropped, 0)
            rows = [json.loads(v) for v in Path(trace).read_text().splitlines()]
            self.assertFalse(any(r["type"] == "receiver_stop" for r in rows))
        finally:
            if child.returncode is None:
                child.kill(); await child.wait()

    async def test_first_read_status_retains_source_time_and_provenance(self):
        await self.request([{"type": "http.request", "body": b"first", "more_body": True},
                            {"type": "http.request", "body": b"second", "more_body": False}])
        value = self.collector.status("request-1")
        self.assertEqual(value["first_read"]["received_body_bytes"], 5)
        self.assertEqual(value["first_read"]["chunk_seq"], 1)
        self.assertLessEqual(value["first_read"]["monotonic_ns"], value["first_read"]["received_monotonic_ns"])

    async def test_no_per_event_fsync(self):
        with patch("receiver_audit.collector.os.fsync") as sync:
            await self.request([{"type": "http.request", "body": b"x"}])
            self.assertEqual(sync.call_count, 0)
            self.collector.flush()
            self.assertEqual(sync.call_count, 1)

    async def test_full_kernel_queue_drops_telemetry_without_blocking_app(self):
        channel = self.telemetry()
        # Do not yield to the collector while overflowing its bounded datagrams.
        channel.emit("watermark")
        channel.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
        for _ in range(100):
            channel.emit("watermark")
        self.assertGreater(channel.dropped, 0)
        delivered = await self.request([{"type": "http.request", "body": b"still-delivered"}], channel=channel)
        self.assertEqual(delivered[0]["body"], b"still-delivered")
        channel.emit("watermark"); self.collector.drain()
        self.assertTrue(any(r.get("reason") == "initial_or_lost_telemetry" for r in self.rows()))

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "root control socket test")
    async def test_real_control_status_and_finalize(self):
        # Real protected directories, socket peer credentials and control API.
        with tempfile.TemporaryDirectory(prefix="argus-receiver-test-", dir="/run") as directory:
            root = Path(directory)
            (root / "data").mkdir(mode=0o700); (root / "control").mkdir(mode=0o700)
            b = root / "binding.json"; b.write_text(json.dumps(binding())); b.chmod(0o600)
            args = types.SimpleNamespace(binding=str(b), socket=str(root / "data/audit.sock"),
                control=str(root / "control/collector.sock"), output=str(root / "out.jsonl"), socket_gid=0)
            task = asyncio.create_task(serve(args))
            channel = self.telemetry(args.socket)
            try:
                async with asyncio.timeout(3):
                    while not Path(args.control).exists():
                        if task.done(): await task
                        await asyncio.sleep(.01)
                await self.request([{"type": "http.request", "body": b"observed"}], channel=channel)
                value = await command(args.control, {"op": "status", "request_id": "request-1"})
                self.assertEqual(value["first_read"]["received_body_bytes"], 8)
                self.assertEqual(value["first_read"]["process"]["pid"], os.getpid())
                await channel.close()
                result = await command(args.control, {"op": "finalize"})
                self.assertTrue(result["finalized"])
                await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                    try: await task
                    except asyncio.CancelledError: pass


class StartupTests(unittest.TestCase):
    def test_changed_upstream_version_entry_or_file_fails_before_server_import(self):
        entry = types.SimpleNamespace(group="console_scripts", name="openviking-server", value="openviking_cli.server_bootstrap:main")
        distribution = types.SimpleNamespace(version="999", entry_points=[entry])
        with patch("receiver_audit.startup.importlib.metadata.distribution", return_value=distribution):
            with self.assertRaisesRegex(RuntimeError, "pinned OpenViking version"):
                verify_upstream()
            distribution.version = "0.4.8"; entry.value = "changed:entry"
            with self.assertRaisesRegex(RuntimeError, "console entry changed"):
                verify_upstream()
            entry.value = "openviking_cli.server_bootstrap:main"
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source.py"; source.write_text("changed startup")
                distribution.locate_file = lambda name: source
                with self.assertRaisesRegex(RuntimeError, "differs from the pinned source"):
                    verify_upstream()

    def test_wrapper_rejects_workers_factory_and_reload(self):
        wrapped = wrap_uvicorn(lambda *a, **k: None, "/audit.sock", "run-1")
        for app, options in (("app:factory", {}), (object(), {"workers": 2}), (object(), {"reload": True}), (object(), {"factory": True})):
            with self.assertRaises(RuntimeError): wrapped(app, **options)

    def test_wrapper_preserves_uvicorn_options(self):
        calls = []
        wrapped = wrap_uvicorn(lambda *a, **k: calls.append((a, k)), "/audit.sock", "run-1")
        app = object(); wrapped(app, host="127.0.0.1", port=1933, log_config=None)
        self.assertIs(calls[0][0][0].app, app)
        self.assertEqual(calls[0][1], {"host": "127.0.0.1", "port": 1933, "log_config": None})


if __name__ == "__main__": unittest.main()
