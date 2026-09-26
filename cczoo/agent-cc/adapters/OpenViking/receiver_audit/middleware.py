"""Best-effort ASGI read telemetry; audit failure never gates the application.

Only metadata is sent. Sequence gaps and watermarks delimit evidence loss;
there is no lossless observation guarantee across crashes.
"""
import asyncio
import json
import re
import socket
import time
import uuid

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
MAX_FRAME = 4096


class Telemetry:
    def __init__(self, socket_path, run_id, interval=.25):
        self.socket_path, self.run_id, self.interval = socket_path, run_id, interval
        self.source_id = uuid.uuid4().hex
        self.started_at_ms = time.time_ns() // 1000000
        self.started_monotonic_ns = time.monotonic_ns()
        self.sequence = self.dropped = 0
        self.socket = self.task = None

    def emit(self, op, **fields):
        """One bounded nonblocking send, without retry or acknowledgement."""
        self.sequence += 1
        value = dict(schema_version=2, op=op, run_id=self.run_id,
                     source_id=self.source_id, source_seq=self.sequence,
                     at_ms=time.time_ns() // 1000000, monotonic_ns=time.monotonic_ns(),
                     source_started_at_ms=self.started_at_ms,
                     source_started_monotonic_ns=self.started_monotonic_ns,
                     dropped=self.dropped, **fields)
        try:
            if self.socket is None:
                self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                self.socket.setblocking(False)
            raw = json.dumps(value, separators=(",", ":")).encode()
            if len(raw) > MAX_FRAME:
                raise ValueError("telemetry frame too large")
            self.socket.sendto(raw, self.socket_path)
        except (OSError, ValueError):
            self.dropped += 1

    def start(self):
        if self.task is None:
            self.emit("watermark")
            self.task = asyncio.create_task(self._heartbeats())

    async def _heartbeats(self):
        while True:
            await asyncio.sleep(self.interval)
            self.emit("watermark")

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.emit("watermark")
        self.emit("source_stop")
        if self.socket:
            self.socket.close()
            self.socket = None


class ReceiverAudit:
    def __init__(self, app, socket_path, run_id, *, telemetry=None):
        if not ID.fullmatch(run_id) or not socket_path.startswith("/"):
            raise ValueError("absolute audit socket and safe run ID required")
        self.app, self.run_id = app, run_id
        self.telemetry = telemetry or Telemetry(socket_path, run_id)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "lifespan"):
            return await self.app(scope, receive, send)
        self.telemetry.start()
        if scope["type"] == "lifespan":
            try:
                return await self.app(scope, receive, send)
            finally:
                await self.telemetry.close()
        # Invalid/duplicate correlation tags never reject business requests.
        tags, duplicate = {}, False
        for key, value in scope.get("headers", []):
            if key.lower() in (b"x-argus-run-id", b"x-argus-request-id"):
                name = key.lower().decode("ascii")
                duplicate |= name in tags
                tags[name] = value.decode("ascii", errors="replace")
        supplied = tags.get("x-argus-request-id", "")
        correlated = not duplicate and tags.get("x-argus-run-id") == self.run_id and bool(ID.fullmatch(supplied))
        base = dict(request_id=supplied if correlated else uuid.uuid4().hex, stream_id=uuid.uuid4().hex)
        self.telemetry.emit("begin", correlated=correlated, **base)
        sequence = 0

        async def audited_receive():
            nonlocal sequence
            sequence += 1
            chunk = sequence
            self.telemetry.emit("pending", chunk_seq=chunk, **base)
            message = await receive()
            # No await between read completion, metadata send and original return.
            body = message.get("body", b"")
            if message.get("type") in ("http.request", "http.disconnect") and isinstance(body, bytes):
                self.telemetry.emit("observed", chunk_seq=chunk,
                                    message_type=message["type"], body_bytes=len(body),
                                    more_body=bool(message.get("more_body", False)), **base)
            return message

        try:
            return await self.app(scope, audited_receive, send)
        finally:
            self.telemetry.emit("end", **base)
