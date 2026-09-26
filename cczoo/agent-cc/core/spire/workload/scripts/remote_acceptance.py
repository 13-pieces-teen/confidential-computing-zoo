#!/usr/bin/env python3
"""Explicit faults, real HTTPS traces, and independent receiver evidence checks.

Run probe on the client and fault on the service host. This tool does not admit,
restart, replace, or recover a workload. See REMOTE_ACCEPTANCE.md before use.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid


def now_ms():
    return time.time_ns() // 1_000_000


def clock_id():
    """A boot identifies a monotonic clock domain; never compare hosts' counters."""
    try:
        return "boot:" + Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    except OSError:
        return None  # Wall time plus the measured cross-host uncertainty is required.


def clocks():
    return {"at_ms": now_ms(), "monotonic_ns": time.monotonic_ns(), "clock_id": clock_id()}


def json_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def receiver_rows(path, run_id):
    """Retain actual receipts and explicitly mark unreadable/truncated evidence."""
    rows, damaged = [], False
    try:
        raw = Path(path).read_bytes()
        for line in raw.splitlines(keepends=True):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict) or not line.endswith(b"\n"):
                    damaged = True
                if isinstance(value, dict):
                    rows.append(value)
            except (ValueError, UnicodeDecodeError):
                damaged = True
    except OSError:
        damaged = True
    if damaged:
        rows.append({"type": "receiver_gap", "run_id": run_id, "code": "unreadable_or_incomplete_file"})
    return rows


def probe_rows(path, run_id):
    """Keep durable positive chunks if a killed probe leaves an incomplete tail."""
    rows = receiver_rows(path, run_id)
    for row in rows:
        if row.get("type") == "receiver_gap" and row.get("code") == "unreadable_or_incomplete_file":
            row["type"] = "probe_gap"
    return rows


def fault_checkpoint(path):
    """Recover a durable checkpoint if interruption truncated only the last line.

    Complete lines must always parse. A malformed terminated last line is
    corruption, not an interrupted write. Observation gaps are handled separately.
    """
    data = Path(path).read_bytes()
    lines = data.splitlines(keepends=True)
    records = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            text = line.decode("utf-8")
            remaining = text[error.pos:].strip()
            incomplete = (error.msg.startswith("Unterminated string") or
                          (error.msg in ("Expecting value", "Expecting property name enclosed in double quotes",
                                         "Expecting ',' delimiter", "Expecting ':' delimiter") and not remaining) or
                          (error.msg == "Expecting value" and remaining in ("t", "tr", "tru", "f", "fa", "fal", "fals", "n", "nu", "nul")))
            if index == len(lines) - 1 and not line.endswith((b"\n", b"\r")) and text.lstrip().startswith("{") and incomplete:
                break
            raise ValueError("fault checkpoint contains a corrupted complete line") from None
        except UnicodeDecodeError:
            # The writer uses json.dumps' ASCII escaping, so a partial UTF-8
            # character cannot be an interrupted checkpoint produced here.
            raise ValueError("fault checkpoint contains invalid encoding") from None
        if not isinstance(value, dict) or value.get("type") != "fault":
            raise ValueError("fault checkpoint line is not a fault record")
        records.append(value)
    if not records:
        raise ValueError("fault file has no complete checkpoint")
    return records[-1]


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as out:
        out.write(json.dumps(value, indent=2) + "\n")


HOLD_BYTES = b"# Temporary Argus acceptance observation only.\n[Service]\nRestart=no\n"


def hold_path(deployment):
    unit = deployment.unit("helper")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit):
        raise ValueError("invalid Helper unit")
    return Path("/run/systemd/system") / (unit + ".d") / "90-argus-acceptance-no-restart.conf"


def hold_contents(owner):
    if not re.fullmatch(r"[0-9a-f]{32}", owner):
        raise ValueError("invalid experiment ownership token")
    return ("# Argus acceptance owner=" + owner + "\n").encode() + HOLD_BYTES


def hold_recovery(deployment, run, owner):
    path = hold_path(deployment)
    contents = hold_contents(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("experiment drop-in directory must not be a symlink")
    with path.open("xb") as output:
        output.write(contents)
        output.flush()
        os.fsync(output.fileno())
    try:
        run(["systemctl", "daemon-reload"])
        if run(["systemctl", "show", deployment.unit("helper"), "--property=Restart", "--value"]) != "no":
            raise ValueError("Helper restart suppression was not applied")
    except Exception:
        if path.read_bytes() == contents:
            path.unlink()
            run(["systemctl", "daemon-reload"])
        raise
    return {"verified": True, "restart": "no", "owner": owner, "path": str(path), "sha256": hashlib.sha256(contents).hexdigest()}


class PinnedConnection(http.client.HTTPSConnection):
    """The old-connection lane must never silently reconnect after TLS closure."""
    def __init__(self, *args, server_id, once=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.server_id, self.once, self.connections = server_id, once, 0
        self.serial = None
        self.peer_sha256 = None
        self.peer_verified = False

    def connect(self):
        if self.once and self.connections:
            raise ConnectionError("original TLS connection closed; automatic reconnection is disabled")
        super().connect()
        self.connections += 1
        cert = self.sock.getpeercert()
        uris = [value for kind, value in cert.get("subjectAltName", ()) if kind == "URI"]
        if uris != [self.server_id]:
            self.close()
            raise ssl.SSLCertVerificationError("peer URI SAN does not match the exact configured SPIFFE ID")
        self.serial = cert.get("serialNumber")
        self.peer_sha256 = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest()
        self.peer_verified = True


def read_response(response, row, conn, emit, marker=None, *, limit=1_048_576, chunk_size=16_384):
    """Observe returned HTTP body bytes, including partial reads before an error.

    No response content is emitted. read1 avoids waiting to fill a whole response
    before timestamping it. These are Python client-read boundaries, not server
    sends, network arrival timestamps, model consumption, or plaintext erasure.
    """
    row.update(http_status=response.status, response_bytes=0, response_chunks=0,
               response_observation_version=1, response_complete=False,
               sentinel_response=False, server_serial=conn.serial)
    tail = b""

    def observed(data, started):
        nonlocal tail
        if not data:
            return
        row["response_bytes"] += len(data)
        row["response_chunks"] += 1
        if marker:
            combined = tail + data
            row["sentinel_response"] |= marker in combined
            tail = combined[-max(0, len(marker) - 1):] if len(marker) > 1 else b""
        emit({"type": "response_chunk", "run_id": row["run_id"], "request_id": row["request_id"],
              "lane": row["lane"], "chunk_seq": row["response_chunks"], "response_bytes": len(data),
              "http_status": response.status, "synthetic_marker_seen": row["sentinel_response"],
              "read_started_at_ms": started["at_ms"], "read_started_monotonic_ns": started["monotonic_ns"],
              **clocks(), "boundary": "client_http_body_read", "peer_verified": conn.peer_verified,
              "server_id": conn.server_id, "server_serial": conn.serial, "peer_sha256": conn.peer_sha256,
              "tls_connections": conn.connections})

    while True:
        started = clocks()
        try:
            data = response.read1(min(chunk_size, limit + 1 - row["response_bytes"]))
        except http.client.IncompleteRead as error:
            observed(error.partial, started)
            raise
        observed(data, started)
        if row["response_bytes"] > limit:
            row.update(ok=False, error="response exceeded the 1 MiB observation limit", incomplete=True)
            conn.close()
            return
        if not data:
            if response.length not in (None, 0):
                raise http.client.IncompleteRead(b"", response.length)
            row.update(response_complete=True, ok=200 <= response.status < 300)
            return


def connection_factory(args):
    parsed = urlsplit(args.url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("a credential-free HTTPS business URL is required")
    if not re.fullmatch(r"spiffe://[a-z0-9._-]+/[A-Za-z0-9/_.-]+", args.server_id):
        raise ValueError("an exact SPIFFE server ID is required")
    context = ssl.create_default_context(cafile=args.bundle)
    context.check_hostname = False  # X.509 chain validation remains mandatory; URI SAN is checked above.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    return (lambda once: PinnedConnection(parsed.hostname, parsed.port or 443, context=context,
                                           timeout=args.timeout, server_id=args.server_id, once=once)), path


def probe(args):
    factory, path = connection_factory(args)
    body = Path(args.body_file).read_bytes() if args.body_file else b""
    if len(body) > 1_048_576:
        raise ValueError("probe request body exceeds 1 MiB")
    if args.method == "GET" and body:
        raise ValueError("GET probes cannot have a body; use explicit POST for an application-approved sentinel endpoint")
    marker = args.response_marker.encode() if args.response_marker else None
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    if args.api_key_env and not api_key:
        raise ValueError("configured API key environment variable is empty")
    lock = threading.Lock()
    errors = []
    deadline = time.monotonic() + args.duration
    with Path(args.output).open("x", encoding="utf-8") as output:
        def emit(row):
            with lock:
                output.write(json.dumps(row) + "\n")
                output.flush()
        emit({"type": "probe_start", "run_id": args.run_id, **clocks(),
              "interval_ms": int(args.interval * 1000), "timeout_ms": int(args.timeout * 1000),
              "method": args.method, "server_id": args.server_id, "body_sha256": hashlib.sha256(body).hexdigest(),
              "response_observation_version": 1,
              "inflight_required": bool(getattr(args, "inflight", False))})
        def inflight():
            """Send one real HTTP body across the observation window, without replay."""
            if args.method != "POST" or len(body) < 2:
                raise ValueError("in-flight probe requires a POST body of at least two bytes")
            conn = factory(True)
            row = {"type": "stream_start", "run_id": args.run_id, "request_id": str(uuid.uuid4()),
                   "lane": "inflight", "started_at_ms": now_ms(), "ok": False, "request_body_bytes": len(body),
                   "response_bytes": 0, "response_observation_version": 1}
            emit(row)
            sent, sent_chunks = 0, 0
            try:
                conn.putrequest("POST", path)
                for name, value in {"X-Argus-Run-ID": args.run_id, "X-Argus-Request-ID": row["request_id"],
                                    "Content-Type": args.content_type, "Transfer-Encoding": "chunked",
                                    "Connection": "close"}.items():
                    conn.putheader(name, value)
                if api_key:
                    conn.putheader("X-API-Key", api_key)
                conn.endheaders()
                size = max(1, (len(body) + 31) // 32)
                chunks = [body[i:i + size] for i in range(0, len(body), size)]
                start = time.monotonic()
                for index, chunk in enumerate(chunks):
                    scheduled = start + (deadline - start) * index / max(1, len(chunks) - 1)
                    time.sleep(max(0, scheduled - time.monotonic()))
                    conn.send(('%x\r\n' % len(chunk)).encode() + chunk + b'\r\n')
                    sent += len(chunk); sent_chunks += 1
                conn.send(b'0\r\n\r\n')
                response = conn.getresponse()
                read_response(response, row, conn, emit, marker)
            except (OSError, ValueError, http.client.HTTPException) as error:
                row['error'] = type(error).__name__
            finally:
                row.update(type="request", completed_at_ms=now_ms(), tls_connections=conn.connections,
                           sent_body_bytes=sent, sent_chunks=sent_chunks)
                emit(row)
                conn.close()
        def lane(name):
            original = factory(True) if name == "existing" else None
            while time.monotonic() < deadline:
                conn = original if original else factory(False)
                row = {"type": "request", "run_id": args.run_id, "request_id": str(uuid.uuid4()),
                       "lane": name, "started_at_ms": now_ms(), "ok": False, "response_bytes": 0,
                       "sentinel_response": False, "request_body_bytes": len(body),
                       "response_observation_version": 1}
                headers = {"X-Argus-Run-ID": args.run_id, "X-Argus-Request-ID": row["request_id"],
                           "Content-Type": args.content_type, "Connection": "keep-alive"}
                if api_key:
                    headers["X-API-Key"] = api_key
                try:
                    conn.request(args.method, path, body=body if body else None, headers=headers)
                    response = conn.getresponse()
                    read_response(response, row, conn, emit, marker)
                except (OSError, ValueError, http.client.HTTPException) as error:
                    # Never record request headers, secret bodies, keys, or arbitrary exception text.
                    row["error"] = type(error).__name__
                    conn.close()
                finally:
                    row.update(completed_at_ms=now_ms(), tls_connections=conn.connections)
                    emit(row)
                    if not original:
                        conn.close()
                time.sleep(args.interval)
            if original:
                original.close()
        def guarded_lane(name):
            try:
                inflight() if name == "inflight" else lane(name)
            except Exception as error:
                with lock:
                    errors.append({"lane": name, "error": type(error).__name__})
        names = ("existing", "new", "inflight") if getattr(args, 'inflight', False) else ("existing", "new")
        threads = [threading.Thread(target=guarded_lane, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        emit({"type": "probe_stop", "run_id": args.run_id, **clocks(), "complete": not errors, "errors": errors})
    if errors:
        raise RuntimeError("probe worker failed; trace is incomplete")


def fault(args):
    if not args.execute_fault:
        raise ValueError("fault execution requires --execute-fault; it disrupts the current deployment")
    import workload
    c = json.loads(workload.protected_file(args.config).read_text())
    d = workload.Deployment(c)
    target = json.loads(workload.run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
    if args.event == "target-exit" and args.hold_recovery:
        policy = json.loads(workload.run(["docker", "inspect", "--format", "{{json .HostConfig.RestartPolicy}}", target["container_id"]]))
        if policy.get("Name") not in ("", "no"):
            raise ValueError("target container has automatic restart enabled; use an isolated no-restart experiment deployment")
    command = (["docker", "kill", target["container_id"]] if args.event == "target-exit" else
               ["systemctl", "kill", "--kill-who=main", "--signal=" +
                ("SIGSTOP" if args.event == "helper-freeze" else "SIGKILL"), d.unit("helper")])
    # Open the evidence file before the mutation. Never overwrite an earlier run.
    with Path(args.output).open("x", encoding="utf-8") as out:
        record = {"type": "fault", "run_id": args.run_id, "event": args.event,
                  "target": target, "started_at_ms": now_ms(), "started_monotonic_ns": time.monotonic_ns(),
                  "clock_id": clock_id(), "executed": False}
        def checkpoint():
            out.write(json.dumps(record) + "\n")
            out.flush()
            os.fsync(out.fileno())
        if args.hold_recovery:
            owner = uuid.uuid4().hex
            record["recovery_hold"] = {"verified": False, "owner": owner, "path": str(hold_path(d)),
                                       "sha256": hashlib.sha256(hold_contents(owner)).hexdigest(), "restart": "no"}
            # Ownership is durable before creating the file, so interrupted
            # runs can release only their own uniquely marked drop-in.
            checkpoint()
            record["recovery_hold"] = hold_recovery(d, workload.run, owner)
        # The measured interval begins immediately before the fault command,
        # after optional restart control has been verified.
        record["started_at_ms"] = now_ms()
        record["started_monotonic_ns"] = time.monotonic_ns()
        checkpoint()
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
        record.update(completed_at_ms=now_ms(), completed_monotonic_ns=time.monotonic_ns(),
                      executed=result.returncode == 0, exit_code=result.returncode)
        checkpoint()
    print(json.dumps(record))
    if result.returncode:
        raise RuntimeError("fault command failed; inspect local systemd/Docker evidence")


def release(args):
    """Remove only this run's exact experiment drop-in; do not restart services."""
    import workload
    c = json.loads(workload.protected_file(args.config).read_text())
    d = workload.Deployment(c)
    evidence = fault_checkpoint(args.fault).get("recovery_hold", {})
    path = hold_path(d)
    contents = hold_contents(evidence.get("owner", ""))
    if evidence.get("path") != str(path) or evidence.get("sha256") != hashlib.sha256(contents).hexdigest():
        raise ValueError("fault record does not own this restart-control file")
    if path.parent.is_symlink() or path.is_symlink() or path.read_bytes() != contents:
        raise ValueError("restart-control file differs; inspect it before restoring the experiment")
    path.unlink()
    workload.run(["systemctl", "daemon-reload"])
    print(json.dumps({"restart_hold_removed": str(path), "services_restarted": False, "at_ms": now_ms()}))


def replacement(args):
    """Observe an operator-performed replacement; never launch or admit it here."""
    import workload
    c = json.loads(workload.protected_file(args.config).read_text())
    d = workload.Deployment(c)
    old = fault_checkpoint(args.fault)
    current = json.loads(workload.run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
    if old.get("run_id") != args.run_id or not old.get("executed"):
        raise ValueError("matching executed fault evidence is required")
    if current["launch_id"] == old["target"]["launch_id"] or current["container_id"] == old["target"]["container_id"]:
        raise ValueError("replacement must have a new measured launch and container ID")
    save(args.output, {"type": "replacement", "run_id": args.run_id, "at_ms": now_ms(),
                       "previous_target": old["target"], "target": current,
                       "admission": "UNKNOWN", "business_recovery": "NOT_RUN"})


def receiver_integrity(logs, target):
    """Require the durable ASGI collector protocol before claiming zero receipt.

    Older hand-authored receipt contracts may show a violation but cannot prove
    absence. An incomplete collector never turns missing receipts into zeros.
    """
    if not logs or any(type(row.get("schema_version")) is not int or row["schema_version"] != 1 for row in logs):
        return "receiver lacks versioned application-read collection evidence"
    if any(type(row.get("record_seq")) is not int for row in logs) or [row.get("record_seq") for row in logs] != list(range(1, len(logs) + 1)):
        return "receiver sequence missing or duplicated"
    if len({row.get("collector_id") for row in logs}) != 1 or not logs[0].get("collector_id"):
        return "receiver collector identity changed"
    if any(type(row.get("at_ms")) is not int for row in logs) or any(a["at_ms"] > b["at_ms"] for a, b in zip(logs, logs[1:])):
        return "receiver clock reversed or is missing"
    first, last = logs[0], logs[-1]
    if first.get("type") != "receiver_start" or first.get("boundary") != "asgi_application_read" or last.get("type") != "receiver_stop" or last.get("complete") is not True:
        return "receiver application-read window not finalized"
    binding = first.get("binding", {})
    if (not isinstance(target, dict) or not target.get("container_id") or
            binding.get("instance_id") != target.get("container_id") or
            binding.get("launch_id") != target.get("launch_id") or
            str(binding.get("process", {}).get("pid")) != str(target.get("pid")) or
            str(binding.get("process", {}).get("start_time")) != str(target.get("start_time")) or
            any(row.get("instance_id") != target["container_id"] for row in logs)):
        return "receiver is not bound to the fault target instance"
    streams, request_ids = {}, set()
    for row in logs[1:-1]:
        kind, stream = row.get("type"), row.get("stream_id")
        if kind == "received" and row.get("phase") == "request_enter":
            rid = row.get("request_id")
            if not stream or stream in streams or not rid or rid in request_ids or row.get("chunk_seq") != 0 or row.get("received_body_bytes") != 0:
                return "receiver request entry duplicated or malformed"
            streams[stream] = {"rid": rid, "seq": 0, "pending": None, "closed": False}
            request_ids.add(rid)
            continue
        state = streams.get(stream)
        if not state or state["closed"] or row.get("request_id") != state["rid"]:
            return "receiver gap, unbound event or event after request close"
        if kind == "receive_pending":
            if state["pending"] is not None or row.get("chunk_seq") != state["seq"] + 1:
                return "receiver pending sequence mismatch"
            state["pending"] = row["chunk_seq"]
        elif kind == "received" and row.get("phase") == "body_read":
            if (state["pending"] is None or row.get("chunk_seq") != state["pending"] or
                    type(row.get("received_body_bytes")) is not int or row["received_body_bytes"] < 0 or
                    row.get("message_type") != "http.request" or type(row.get("more_body")) is not bool):
                return "receiver read incomplete or malformed"
            state.update(seq=row["chunk_seq"], pending=None, more=row["more_body"])
        elif kind == "request_end":
            if row.get("complete") is not True or state["pending"] is not None or state.get("more"):
                return "receiver request unfinished"
            state["closed"] = True
        else:
            return "receiver unknown event or coverage gap"
    if any(not value["closed"] for value in streams.values()) or last.get("pending_streams") != 0 or last.get("issues") != 0 or last.get("requests") != len(streams):
        return "receiver unfinished streams or inconsistent final counters"
    return None


def same_target(binding, target):
    return (isinstance(binding, dict) and isinstance(target, dict) and target.get('container_id')
            and binding.get('instance_id') == target.get('container_id')
            and binding.get('launch_id') == target.get('launch_id')
            and str(binding.get('process', {}).get('pid')) == str(target.get('pid'))
            and str(binding.get('process', {}).get('start_time')) == str(target.get('start_time')))


def clock_domain(row):
    """Older receiver records identify their host boot in process provenance."""
    if row.get('clock_id'):
        return row['clock_id']
    process = row.get('process') or row.get('binding', {}).get('process') or row.get('target') or {}
    boot = process.get('boot_id')
    return 'boot:' + boot.replace('-', '') if isinstance(boot, str) and boot else None


def same_clock_domain(row, event):
    domain = clock_domain(row)
    return bool(domain and domain == clock_domain(event))


def after_bound(row, event, bound_ms, uncertainty_ms):
    if same_clock_domain(row, event) and type(row.get('monotonic_ns')) is int and type(event.get('started_monotonic_ns')) is int:
        return row['monotonic_ns'] >= event['started_monotonic_ns'] + bound_ms * 1_000_000
    return type(row.get('at_ms')) is int and row['at_ms'] >= event['started_at_ms'] + bound_ms + uncertainty_ms


def ambiguous_bound(row, event, bound_ms, uncertainty_ms):
    if same_clock_domain(row, event) and type(row.get('monotonic_ns')) is int and type(event.get('started_monotonic_ns')) is int:
        return False
    at = row.get('at_ms')
    cutoff = event['started_at_ms'] + bound_ms
    return type(at) is int and cutoff - uncertainty_ms <= at < cutoff + uncertainty_ms


def response_observations(rows, event, *, bound_ms, uncertainty_ms):
    """Client-read evidence is separate from request handling and server sends."""
    run = event.get("run_id")
    rows = [r for r in rows if r.get("run_id") == run]
    starts = [r for r in rows if r.get("type") == "probe_start"]
    unknown = {"client_response_delivery": "UNKNOWN", "client_response_post_bound_bytes": 0,
               "client_response_ambiguous_bytes": 0,
               "client_response_scope": "successful business response or synthetic marker at client HTTP reads; error responses counted separately"}
    if len(starts) != 1 or starts[0].get("response_observation_version") != 1:
        return unknown | {"client_response_delivery": "NOT_RUN"}
    samples = {r["request_id"]: r for r in rows if r.get("type") == "request" and r.get("request_id")}
    chunks = [r for r in rows if r.get("type") == "response_chunk"]
    seen, totals, damaged, late, ambiguous, other_bytes = {}, {}, False, [], [], 0
    held = event.get("recovery_hold", {}).get("verified") is True and event.get("recovery_hold", {}).get("restart") == "no"
    for chunk in chunks:
        request = samples.get(chunk.get("request_id"))
        # A process may be interrupted after a durable chunk but before its final
        # request row. Preserve the positive read, while refusing absence claims.
        if request is None:
            damaged = True
        valid = (isinstance(chunk.get("request_id"), str) and bool(chunk["request_id"])
                 and chunk.get("lane") in ("new", "existing", "inflight")
                 and (request is None or chunk.get("lane") == request.get("lane"))
                 and chunk.get("boundary") == "client_http_body_read" and chunk.get("peer_verified") is True
                 and chunk.get("server_id") == starts[0].get("server_id")
                 and isinstance(chunk.get("peer_sha256"), str) and re.fullmatch("[0-9a-f]{64}", chunk["peer_sha256"])
                 and type(chunk.get("response_bytes")) is int and chunk["response_bytes"] > 0
                 and type(chunk.get("at_ms")) is int and type(chunk.get("chunk_seq")) is int
                 and type(chunk.get("http_status")) is int)
        if not valid:
            damaged = True
            continue
        rid = chunk["request_id"]
        if chunk["chunk_seq"] != seen.get(rid, 0) + 1:
            damaged = True
        seen[rid] = chunk["chunk_seq"]
        totals[rid] = totals.get(rid, 0) + chunk["response_bytes"]
        if not (200 <= chunk["http_status"] < 300 or chunk.get("synthetic_marker_seen") is True):
            other_bytes += chunk["response_bytes"]
            continue
        if not held and chunk["lane"] not in ("existing", "inflight"):
            continue
        same_clock = (chunk.get("clock_id") and chunk.get("clock_id") == event.get("clock_id")
                      and type(chunk.get("monotonic_ns")) is int and type(event.get("started_monotonic_ns")) is int)
        delta = ((chunk["monotonic_ns"] - event["started_monotonic_ns"]) / 1_000_000 if same_clock
                 else chunk["at_ms"] - event["started_at_ms"])
        margin = 0 if same_clock else uncertainty_ms
        if delta >= bound_ms + margin:
            late.append(chunk)
        elif delta >= bound_ms - margin:
            ambiguous.append(chunk)
    result = unknown | {"client_response_post_bound_bytes": sum(r["response_bytes"] for r in late),
                        "client_response_ambiguous_bytes": sum(r["response_bytes"] for r in ambiguous),
                        "client_response_non_success_observed_bytes": other_bytes}
    if late:
        return result | {"client_response_delivery": "FAIL"}
    stops = [r for r in rows if r.get("type") == "probe_stop"]
    complete = (len(stops) == 1 and stops[0].get("complete") is True and samples and held
                and not any(r.get("type") == "probe_gap" for r in rows)
                and not damaged and not ambiguous
                and all(r.get("response_observation_version") == 1 and not r.get("incomplete")
                        and r.get("response_bytes", 0) == totals.get(rid, 0) for rid, r in samples.items()))
    return result | {"client_response_delivery": "PASS" if complete else "UNKNOWN"}


def receiver_v2(logs, event, samples, stream_starts, *, bound_ms, uncertainty_ms, inflight_required):
    """A measurement gap affects its interval, never authorizes or blocks traffic."""
    unknown = {'receiver_delivery': 'UNKNOWN', 'inflight_delivery': 'UNKNOWN' if inflight_required else 'NOT_RUN'}
    starts = [r for r in logs if r.get('type') == 'receiver_start']
    if len(starts) != 1 or not same_target(starts[0].get('binding'), event.get('target')):
        return unknown | {'reason': 'receiver initial deployment does not match fault target'}
    collector = starts[0].get('collector_id')
    damaged = any(r.get('type') == 'receiver_gap' and r.get('code') == 'unreadable_or_incomplete_file' for r in logs)
    logs = [r for r in logs if r.get('collector_id') == collector and r.get('schema_version') == 2]
    bindings = [starts[0]['binding']] + [r['binding'] for r in logs if r.get('type') == 'target_added' and isinstance(r.get('binding'), dict)]
    allowed = {(b.get('instance_id'), b.get('launch_id')) for b in bindings}
    sources = {r['source_id']: r for r in logs if r.get('type') == 'source_seen' and r.get('source_id')
               and (r.get('instance_id'), r.get('launch_id')) in allowed and r.get('process')}
    def bound(row):
        source = sources.get(row.get('source_id'), {})
        return (source and row.get('provenance') == 'kernel_process_and_deployment'
                and row.get('instance_id') == source.get('instance_id') and row.get('launch_id') == source.get('launch_id')
                and row.get('process') == source.get('process'))
    request_ids = {r['request_id'] for r in samples + stream_starts}
    receipts = [r for r in logs if r.get('type') == 'received' and r.get('request_id') in request_ids and bound(r)]
    held = event.get('recovery_hold', {}).get('verified') is True and event.get('recovery_hold', {}).get('restart') == 'no'
    old_ids = {r['request_id'] for r in samples + stream_starts if r.get('lane') in ('existing', 'inflight')}
    violations = [r for r in receipts if after_bound(r, event, bound_ms, uncertainty_ms)
                  and (held or r['request_id'] in old_ids)
                  and (r.get('phase') == 'request_enter' or (r.get('phase') == 'body_read'
                       and r.get('message_type') == 'http.request' and type(r.get('received_body_bytes')) is int
                       and r['received_body_bytes'] > 0))]
    if violations:
        return {'receiver_delivery': 'FAIL', 'inflight_delivery': 'FAIL' if any(r['request_id'] in {s['request_id'] for s in stream_starts} for r in violations) else unknown['inflight_delivery'],
                'received_body_bytes': sum(r.get('received_body_bytes', 0) for r in violations),
                'delivered_requests': len({r['request_id'] for r in violations}),
                'observed_instances': sorted({r['instance_id'] for r in violations}),
                'reason': 'application read observed after the stop bound, including in-flight bodies'}
    if any(ambiguous_bound(r, event, bound_ms, uncertainty_ms) for r in receipts):
        return unknown | {'reason': 'receiver read overlaps the cross-host clock uncertainty around the stop bound'}
    if not held:
        return unknown | {'reason': 'automatic re-admission was not excluded'}
    if damaged:
        return unknown | {'reason': 'receiver file has an unlocatable damaged interval'}
    baseline = {r['request_id'] for r in samples if r.get('ok') and r['completed_at_ms'] < event['started_at_ms'] - uncertainty_ms}
    if not baseline.issubset({r['request_id'] for r in receipts}):
        return unknown | {'reason': 'healthy baseline was not independently observed'}
    inflight_ids = {r['request_id'] for r in stream_starts}
    if inflight_required and (not inflight_ids or not any(r['request_id'] in inflight_ids and r.get('phase') == 'body_read'
            and r.get('received_body_bytes', 0) > 0 and
            ((r.get('monotonic_ns', float('inf')) < event['started_monotonic_ns']) if same_clock_domain(r, event) and type(event.get('started_monotonic_ns')) is int
             else r['at_ms'] < event['started_at_ms'] - uncertainty_ms) for r in receipts)):
        return unknown | {'reason': 'in-flight request was not read before the fault; proxy buffering may prevent this scenario'}
    if inflight_required and any(r.get('lane') == 'inflight' and r['completed_at_ms'] < event['started_at_ms'] - uncertainty_ms for r in samples):
        return unknown | {'reason': 'slow request ended before the fault was issued'}
    mono = (type(event.get('started_monotonic_ns')) is int and same_clock_domain(starts[0], event)
            and all(same_clock_domain(source, event) for source in sources.values()))
    field = 'monotonic_ns' if mono else 'at_ms'
    start = event['started_monotonic_ns'] + bound_ms * 1_000_000 if mono else event['started_at_ms'] + bound_ms + uncertainty_ms
    end_wall = max(r['completed_at_ms'] for r in samples) + uncertainty_ms
    end = event['started_monotonic_ns'] + (end_wall - event['started_at_ms']) * 1_000_000 if mono else end_wall
    def recorded_time(row):
        return row.get('received_' + field, row.get(field))
    # A malformed file or unbound peer cannot establish absence. Sequence gaps
    # outside the measured interval do not invalidate later complete coverage.
    previous = None
    for row in logs:
        seq = row.get('record_seq')
        if type(seq) is not int or seq < 1:
            return unknown | {'reason': 'receiver record sequence is missing'}
        if previous is None and seq != 1:
            return unknown | {'reason': 'receiver initial record is missing'}
        if previous is not None and seq != previous['record_seq'] + 1:
            a, b = recorded_time(previous), recorded_time(row)
            if seq <= previous['record_seq'] or type(a) is not int or type(b) is not int or b < a or (a <= end and b >= start):
                return unknown | {'reason': 'persisted receiver records have a gap in the measured interval'}
        if row.get('type') == 'receiver_gap':
            at = recorded_time(row)
            if type(at) is not int or start <= at <= end:
                return unknown | {'reason': 'receiver has unbound or malformed observations in the measured interval'}
        previous = row
    participating = set(sources)
    # Explicitly registered successors must also be observed. Absence of a
    # source watermark must not become evidence of zero delivery.
    if not participating or any(not any((s.get('instance_id'), s.get('launch_id')) == identity for s in sources.values()) for identity in allowed):
        return unknown | {'reason': 'a registered observation target has no source coverage'}
    original_sources = {r['source_id'] for r in receipts if r['request_id'] in baseline}
    target_process = starts[0]['binding'].get('process', {})
    original_sources.update(key for key, source in sources.items()
                            if all(str(source['process'].get(k)) == str(target_process.get(k)) for k in ('pid', 'start_time')))
    earliest_source = end
    for source_id in participating:
        intervals = [r for r in logs if r.get('type') == 'coverage_interval' and r.get('source_id') == source_id]
        begin_field, end_field = 'started_' + field, 'ended_' + field
        if not intervals or any(type(r.get(begin_field)) is not int or type(r.get(end_field)) is not int or r[end_field] < r[begin_field] for r in intervals):
            return unknown | {'reason': 'receiver coverage lacks a valid interval clock'}
        # source_seen is collector receipt time and can be delayed. Only the
        # source's own interval clocks may delimit its observation lifetime.
        source_start = min(r[begin_field] for r in intervals)
        earliest_source = min(earliest_source, source_start)
        left = start if source_id in original_sources else max(start, source_start)
        if left >= end:
            return unknown | {'reason': 'receiver source has no coverage within the observation window'}
        if any(r.get('status') == 'UNKNOWN' and r[begin_field] < end and r[end_field] > left for r in intervals):
            return unknown | {'reason': 'receiver has an observation gap within the post-bound interval'}
        covered_to = left
        for interval in sorted((r for r in intervals if r.get('status') == 'COMPLETE'), key=lambda r: r[begin_field]):
            if interval[begin_field] <= covered_to:
                covered_to = max(covered_to, interval[end_field])
        if covered_to < end:
            return unknown | {'reason': 'receiver coverage ends before the observation window; crash tail is unknown'}
    if earliest_source > start:
        return unknown | {'reason': 'receiver source coverage starts after the stop bound'}
    return {'receiver_delivery': 'PASS', 'inflight_delivery': 'PASS' if inflight_required else 'NOT_RUN'}


def assess(rows, event, receiver=None, *, bound_ms, clock_uncertainty_ms, min_samples=3, max_gap_ms=3000):
    run = event.get("run_id")
    result = {"run_id": run, "result": "UNKNOWN", "traffic": "UNKNOWN", "receiver_delivery": "NOT_RUN",
              "bound_ms": bound_ms, "clock_uncertainty_ms": clock_uncertainty_ms,
              "scope": "observed application reads after the stop bound; in-flight coverage is reported separately"}
    if not event.get("executed") or not isinstance(event.get("started_at_ms"), int):
        return result | {"reason": "no successful fault command record"}
    result.update(response_observations(rows, event, bound_ms=bound_ms, uncertainty_ms=clock_uncertainty_ms))
    if result["client_response_delivery"] == "FAIL":
        result.update(result="FAIL", traffic="FAIL", reason="client body bytes observed after the stop bound")
    probe_damaged = any(r.get("type") == "probe_gap" for r in rows)
    fault_at = event["started_at_ms"]
    cutoff = fault_at + bound_ms + clock_uncertainty_ms
    samples = [row for row in rows if row.get("type") == "request" and row.get("run_id") == run]
    starts = [row for row in rows if row.get("type") == "probe_start" and row.get("run_id") == run]
    stops = [row for row in rows if row.get("type") == "probe_stop" and row.get("run_id") == run]
    stream_starts = [row for row in rows if row.get('type') == 'stream_start' and row.get('run_id') == run]
    ids = [row.get("request_id") for row in samples]
    if not samples or None in ids or len(ids) != len(set(ids)):
        return result | {"reason": "missing or duplicated request trace"}
    if len(starts) != 1 or len(stops) != 1 or stops[0].get("complete") is not True or starts[0]["at_ms"] > min(row["started_at_ms"] for row in samples) or stops[0]["at_ms"] < max(row["completed_at_ms"] for row in samples):
        return result | {"reason": "probe trace was not completed"}
    late = []
    incomplete = False
    for lane in ("existing", "new"):
        series = sorted([row for row in samples if row.get("lane") == lane], key=lambda row: row["started_at_ms"])
        baseline = [row for row in series if row["completed_at_ms"] < fault_at - clock_uncertainty_ms]
        outage = [row for row in series if row["started_at_ms"] >= cutoff]
        if len(baseline) < min_samples or not all(row["ok"] for row in baseline):
            return result | {"reason": "each TLS lane needs a healthy pre-fault business baseline"}
        if lane == "existing" and any(row.get("tls_connections") != 1 for row in baseline + outage):
            return result | {"reason": "original TLS lane was not established or silently reconnected"}
        if len(outage) < min_samples:
            return result | {"reason": "insufficient post-bound attempts in each TLS lane"}
        if any(b["started_at_ms"] - a["started_at_ms"] > max_gap_ms for a, b in zip(series, series[1:])):
            return result | {"reason": "probe observation gap exceeds the configured limit"}
        incomplete |= any(row.get("incomplete") for row in outage)
        late.extend(outage)
    held = event.get("recovery_hold", {}).get("verified") is True and event.get("recovery_hold", {}).get("restart") == "no"
    old_success = any((row["ok"] or row.get("sentinel_response")) and row["lane"] == "existing" for row in late)
    if old_success or (held and any(row["ok"] or row.get("sentinel_response") for row in late)):
        result.update(result="FAIL", traffic="FAIL", reason="business response observed after the stop bound")
    elif not held:
        result.update(reason="automatic re-admission was not excluded; new TLS success may be legitimate recovery")
    elif incomplete:
        result.update(reason="response observation was truncated")
    elif probe_damaged:
        result.update(reason="probe trace has an incomplete or unreadable interval")
    elif result["traffic"] != "FAIL":
        result["traffic"] = "PASS"
    if receiver is None:
        return result | {"reason": result.get("reason", "independent receiver evidence not supplied")}
    inflight_required = bool(starts[0].get('inflight_required'))
    logs = [row for row in receiver if row.get("run_id") == run]
    if any(row.get('type') == 'receiver_start' and row.get('schema_version') == 2 for row in logs):
        v2 = receiver_v2(logs, event, samples, stream_starts, bound_ms=bound_ms,
                         uncertainty_ms=clock_uncertainty_ms, inflight_required=inflight_required)
        result.update(v2)
        if v2['receiver_delivery'] == 'FAIL':
            result['result'] = 'FAIL'
        elif v2['receiver_delivery'] == 'PASS' and result['traffic'] == 'PASS' and result['client_response_delivery'] != 'UNKNOWN':
            result['result'] = 'PASS'
        elif result['client_response_delivery'] == 'UNKNOWN' and result['result'] != 'FAIL':
            result['reason'] = 'client response boundary has incomplete or clock-ambiguous observations'
        return result
    starts = [row for row in logs if row.get("type") == "receiver_start"]
    stops = [row for row in logs if row.get("type") == "receiver_stop" and row.get("complete") is True]
    receipts = [row for row in logs if row.get("type") == "received"]
    late_ids = {row["request_id"] for row in late}
    target = event.get("target") or {}
    # Positive observations also need target provenance. Missing tail records
    # must not erase a real receipt, but receipts from another instance cannot
    # be attributed to this fault merely by copying a client request header.
    origin = starts[0] if len(starts) == 1 else {}
    binding = origin.get("binding") or {}
    bound = (origin.get("schema_version") == 1 and origin.get("boundary") == "asgi_application_read" and
             target.get("container_id") and binding.get("instance_id") == target.get("container_id") and
             binding.get("launch_id") == target.get("launch_id") and
             str(binding.get("process", {}).get("pid")) == str(target.get("pid")) and
             str(binding.get("process", {}).get("start_time")) == str(target.get("start_time")))
    known_ids = {row['request_id'] for row in samples + stream_starts}
    delivered = [row for row in receipts if bound and row.get("request_id") in known_ids and
                  (after_bound(row, event, bound_ms, clock_uncertainty_ms) or
                   (row.get('at_ms') is None and row.get('request_id') in late_ids)) and
                 row.get("schema_version") == 1 and row.get("collector_id") == origin.get("collector_id") and
                 row.get("instance_id") == target["container_id"] and
                 type(row.get("received_body_bytes")) is int and row["received_body_bytes"] >= 0]
    violation = [row for row in delivered if held or any(sample["request_id"] == row.get("request_id") and sample["lane"] in ("existing", "inflight") for sample in samples + stream_starts)]
    if violation:
        result.update(result="FAIL", receiver_delivery="FAIL", delivered_requests=len({row.get("request_id") for row in violation}),
                      received_body_bytes=sum(row.get("received_body_bytes", 0) for row in violation),
                       reason="the independent application receiver recorded post-bound reads")
        return result
    if bound and any(ambiguous_bound(row, event, bound_ms, clock_uncertainty_ms)
                     for row in receipts if row.get('request_id') in known_ids):
        return result | {'receiver_delivery': 'UNKNOWN',
                         'reason': 'receiver read overlaps the cross-host clock uncertainty around the stop bound'}
    if inflight_required:
        return result | {'receiver_delivery': 'UNKNOWN', 'inflight_delivery': 'UNKNOWN',
                         'reason': 'the requested in-flight scenario requires version 2 receiver coverage'}
    if not held:
        return result | {"receiver_delivery": "UNKNOWN", "reason": result.get("reason", "re-admission boundary not independently established")}
    integrity_error = receiver_integrity(logs, event.get("target"))
    if integrity_error:
        return result | {"receiver_delivery": "UNKNOWN", "reason": integrity_error}
    covered = (len(starts) == len(stops) == 1 and
               starts[0].get("at_ms", float("inf")) <= min(row["started_at_ms"] for row in samples) - clock_uncertainty_ms and
               stops[0].get("at_ms", 0) >= max(row["completed_at_ms"] for row in samples) + clock_uncertainty_ms)
    receipt_ids = {row.get("request_id") for row in receipts}
    baseline_ids = {row["request_id"] for row in samples if row["completed_at_ms"] < fault_at - clock_uncertainty_ms and row["ok"]}
    if not covered or not baseline_ids.issubset(receipt_ids):
        result.update(receiver_delivery="UNKNOWN", reason=result.get("reason", "receiver coverage or healthy-baseline receipts are incomplete"))
        return result
    result["receiver_delivery"] = "PASS"
    if result["traffic"] == "PASS" and result["client_response_delivery"] != "UNKNOWN":
        result["result"] = "PASS"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("probe")
    for name in ("url", "cert", "key", "bundle", "server-id", "run-id", "output"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--duration", type=float, default=40)
    p.add_argument("--interval", type=float, default=.25)
    p.add_argument("--timeout", type=float, default=2)
    p.add_argument("--method", choices=("GET", "POST"), default="GET")
    p.add_argument("--body-file")
    p.add_argument("--inflight", action="store_true", help="also send one slow chunked POST across the fault window")
    p.add_argument("--content-type", default="application/json")
    p.add_argument("--api-key-env", default="")
    p.add_argument("--response-marker", help="nonsecret synthetic marker; never use real private data in experiments")
    p = commands.add_parser("fault")
    for name in ("config", "run-id", "output"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--event", choices=("helper-freeze", "helper-crash", "target-exit"), required=True)
    p.add_argument("--execute-fault", action="store_true")
    p.add_argument("--hold-recovery", action="store_true", help="install temporary Restart=no drop-in; explicitly release it after the trace")
    p = commands.add_parser("release")
    for name in ("config", "fault"):
        p.add_argument("--" + name, required=True)
    p = commands.add_parser("replacement")
    for name in ("config", "run-id", "fault", "output"):
        p.add_argument("--" + name, required=True)
    p = commands.add_parser("check")
    for name in ("trace", "fault", "output"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--receiver")
    p.add_argument("--bound-ms", type=int, required=True)
    p.add_argument("--clock-uncertainty-ms", type=int, required=True)
    p.add_argument("--max-gap-ms", type=int, default=3000)
    args = parser.parse_args()
    if args.command == "probe":
        if not 1 <= args.duration <= 3600 or not .05 <= args.interval <= 5 or not .1 <= args.timeout <= 30:
            parser.error("duration [1,3600], interval [.05,5], timeout [.1,30] seconds required")
        if args.api_key_env and not os.environ.get(args.api_key_env):
            parser.error("configured API key environment variable is empty")
        if args.inflight and (args.method != "POST" or not args.body_file):
            parser.error("--inflight requires a synthetic POST body")
        probe(args)
    elif args.command == "fault":
        fault(args)
    elif args.command == "replacement":
        replacement(args)
    elif args.command == "release":
        release(args)
    else:
        if args.bound_ms <= 0 or args.clock_uncertainty_ms < 0 or args.max_gap_ms <= 0:
            parser.error("positive bound/gap and nonnegative measured clock uncertainty required")
        event = fault_checkpoint(args.fault)
        result = assess(probe_rows(args.trace, event.get("run_id")), event, receiver_rows(args.receiver, event.get("run_id")) if args.receiver else None,
                        bound_ms=args.bound_ms, clock_uncertainty_ms=args.clock_uncertainty_ms, max_gap_ms=args.max_gap_ms)
        save(args.output, result)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["result"] == "PASS" else 1 if result["result"] == "FAIL" else 2)


if __name__ == "__main__":
    main()
