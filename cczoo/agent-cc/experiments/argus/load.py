#!/usr/bin/env python3
"""Bounded open-loop HTTPS sampler for synthetic API workloads; no body logging."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import threading
import time
from urllib.parse import urlsplit

from common import measurement_log, write_measurement, atomic, require, protected_secret, sha
from analysis import summarize_requests, quantile
from client_material import ContextProvider


class TimedHTTPSConnection(http.client.HTTPSConnection):
    """Measure TCP establishment and the actual TLS handshake separately."""
    def connect(self):
        started = time.perf_counter()
        try:
            http.client.HTTPConnection.connect(self)
        finally:
            self.tcp_connect_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        try:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        finally:
            self.tls_handshake_ms = (time.perf_counter() - started) * 1000


class ConnectionSession:
    """One worker's connection. Failed requests are never replayed.

    The publisher is consulted before *every* request, including reuse. A new
    credential generation replaces the connection; an invalid lease closes it.
    This sampler does not replace the application's active-stream fault oracle.
    """
    def __init__(self, worker_id='direct'):
        self.worker_id, self.sequence = worker_id, 0
        self.conn, self.context, self.target, self.connection_id = None, None, None, None
        self.next_reason = 'initial'

    def close(self, reason='previous_error'):
        if self.conn is not None:
            self.conn.close()
        self.conn = None
        self.next_reason = reason

    def acquire(self, parsed, context, peer_id, timeout, prefix, row):
        # Calling the provider can reject an expired lease even with an open TLS
        # connection. Callers invalidate the connection on any such exception.
        current = context() if callable(context) else context
        target = (parsed.hostname, parsed.port or 443, peer_id)
        if self.conn is not None and (current is not self.context or target != self.target):
            self.close('credentials_rotated' if current is not self.context else 'target_changed')
        if self.conn is not None and self.conn.sock is None:
            self.close('server_closed')
        row['worker_id'] = self.worker_id
        if self.conn is None:
            reason = self.next_reason
            self.sequence += 1
            self.connection_id = '%s-%s-c%d' % (prefix, self.worker_id, self.sequence)
            row.update(connection_id=self.connection_id, connection_attempted=True,
                       reconnect=self.sequence > 1 and reason != 'new_per_request', connect_reason=reason)
            self.conn = TimedHTTPSConnection(target[0], target[1], context=current, timeout=timeout)
            self.context, self.target = current, target
            started = time.perf_counter()
            try:
                self.conn.connect()
                row['connection_created'] = True
                row['tls_session_reused'] = self.conn.sock.session_reused
                uris = [v for k, v in self.conn.sock.getpeercert().get('subjectAltName', ()) if k == 'URI']
                require(uris == [peer_id], 'unexpected peer SPIFFE identity')
                row['peer_identity_verified'] = True
                row['peer_spiffe_id'] = peer_id
                # Do not let http.client silently open an unchecked replacement
                # connection inside send(). Reconnect on the next scheduled call.
                self.conn.auto_open = 0
            finally:
                row['connect_ms'] = (time.perf_counter() - started) * 1000
                row['tcp_connect_ms'] = getattr(self.conn, 'tcp_connect_ms', None)
                row['tls_handshake_ms'] = getattr(self.conn, 'tls_handshake_ms', None)
        else:
            row.update(connection_id=self.connection_id, connection_reused=True,
                       peer_identity_verified=True, peer_spiffe_id=peer_id, connect_reason='reused')
            self.conn.sock.settimeout(timeout)
        return self.conn


def memory_result(payload):
    """Count returned leaf memories without persisting their text or URI."""
    try:
        value = json.loads(payload)
        require(isinstance(value, dict) and value.get('status') == 'ok', 'memory API did not succeed')
        result = value.get('result')
        require(isinstance(result, dict), 'missing memory search result')
        items = result.get('memories')
        require(isinstance(items, list), 'missing memory result list')
        leaves = {item['uri'] for item in items if isinstance(item, dict)
                  and isinstance(item.get('uri'), str) and item['uri'].startswith('viking://user/')
                  and item.get('level') == 2}
        return {'memory_result': 'nonempty' if leaves else 'empty', 'memory_leaf_count': len(leaves)}
    except (ValueError, TypeError, KeyError):
        return {'memory_result': 'unknown', 'memory_leaf_count': None}


def request(url, context, peer_id, body, key, timeout, run_id, number, phase, deadline, request_prefix=None,
            session=None, connection_mode='new', workload_kind='custom_api'):
    started = time.perf_counter()
    parsed = urlsplit(url)
    owned_session = session is None
    session = session or ConnectionSession('r%d' % number)
    conn, api_started = None, None
    row = {"run_id": run_id, "request_id": "%s-%d" % (request_prefix or run_id, number), "phase": phase,
           "started_at_ms": time.time_ns() // 1000000, "outcome": "unknown",
           "connection_mode": connection_mode, 'workload_kind': workload_kind,
           'http_method': 'POST' if body is not None else 'GET', 'request_replayed': False,
           'submission_state': 'not_attempted', 'connection_attempted': False,
           'connection_created': False, 'connection_reused': False, 'reconnect': False,
           'connect_ms': 0, 'tcp_connect_ms': 0, 'tls_handshake_ms': 0}
    try:
        require(connection_mode in ('new', 'reuse'), 'invalid connection mode')
        if connection_mode == 'new': session.close('new_per_request')
        conn = session.acquire(parsed, context, peer_id, timeout, request_prefix or run_id, row)
        headers = {"Content-Type": "application/json", "X-Argus-Run-ID": run_id, "X-Argus-Request-ID": row["request_id"]}
        if key:
            headers["X-API-Key"] = key
        api_started = time.perf_counter()
        row['submission_state'] = 'unknown'
        conn.request(row['http_method'], (parsed.path or '/') + ("?" + parsed.query if parsed.query else ""), body=body, headers=headers)
        response = conn.getresponse()
        # Bound response draining; API payloads are not preserved.
        remaining, payload = 4 * 1024 * 1024, bytearray()
        while remaining:
            chunk = response.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            if workload_kind == 'memory_query': payload.extend(chunk)
        require(remaining > 0, "response exceeds bound")
        row['response_bytes'] = 4 * 1024 * 1024 - remaining
        row['submission_state'] = 'response_received'
        row["http_status"] = response.status
        row["outcome"] = "success" if 200 <= response.status < 300 else "rejected" if response.status in (401, 403) else "unknown"
        if workload_kind == 'memory_query' and row['outcome'] == 'success':
            row.update(memory_result(payload))
        if time.monotonic() > deadline:
            row["outcome"] = "timeout"
    except (TimeoutError, __import__("socket").timeout):
        row["outcome"] = "timeout"
        session.close()
    except Exception as exc:
        row["error_class"] = type(exc).__name__
        session.close()
    finally:
        if api_started is not None: row['api_ms'] = (time.perf_counter() - api_started) * 1000
        if connection_mode == 'new' or owned_session: session.close('new_per_request')
    row["latency_ms"] = (time.perf_counter() - started) * 1000
    row["completed_at_ms"] = time.time_ns() // 1000000
    return row


def summarize_connections(rows):
    measured = [r for r in rows if r.get('phase') == 'measurement']
    result = {name: sum(r.get(field) is True for r in measured) for name, field in (
        ('connection_attempts', 'connection_attempted'), ('connections_created', 'connection_created'),
        ('requests_reusing_connection', 'connection_reused'), ('reconnect_attempts', 'reconnect'))}
    result['observed_connection_ids'] = len({r['connection_id'] for r in measured if r.get('connection_id')})
    result['initialization_connections'] = sum(r.get('connection_created') is True for r in rows if r.get('phase') == 'warmup')
    result['reconnect_reasons'] = {reason: sum(r.get('reconnect') and r.get('connect_reason') == reason for r in measured)
                                   for reason in sorted({r['connect_reason'] for r in measured if r.get('reconnect')})}
    for field in ('tcp_connect_ms', 'tls_handshake_ms', 'connect_ms', 'api_ms'):
        values = [r[field] for r in measured if isinstance(r.get(field), (int, float))
                  and (field == 'api_ms' or r.get('connection_attempted'))]
        result[field] = {'n': len(values), 'p50': quantile(values, .5), 'p95': quantile(values, .95)}
    return result


def run(args):
    parsed = urlsplit(args.url)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
            and not parsed.fragment, "mTLS HTTPS endpoint required without userinfo or fragment")
    prefix = getattr(args, "request_prefix", None) or args.run_id
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,239}", prefix) is not None, "invalid request ID prefix")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", args.run_id) is not None, "invalid run ID")
    require(args.rate > 0 and args.concurrency > 0 and args.measure > 0 and args.warmup >= 0, "invalid load")
    require(args.timeout > 0, "positive request timeout required")
    connection_mode = getattr(args, 'connection_mode', 'new')
    require(connection_mode in ('new', 'reuse'), 'connection_mode must be new or reuse')
    workload_kind = getattr(args, 'workload_kind', None) or ('status_api' if parsed.path == '/api/v1/system/status' else 'custom_api')
    require(workload_kind in ('status_api', 'memory_query', 'custom_api'), 'invalid workload kind')
    require(workload_kind != 'status_api' or parsed.path == '/api/v1/system/status', 'status_api requires the system status endpoint')
    directory = Path(args.output); directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not (directory / "requests.jsonl").exists(), "use a fresh output directory")
    if getattr(args, 'credentials_dir', None):
        require(not any(getattr(args, key, None) for key in ('cert', 'key', 'bundle')), 'choose a publisher directory or fixed material')
        context = ContextProvider(args.credentials_dir, getattr(args, 'client_id', None))
        context()
    else:
        require(all(getattr(args, key, None) for key in ('cert', 'key', 'bundle')), 'complete fixed mTLS material required')
        context = ssl.create_default_context(cafile=args.bundle)
        context.check_hostname = False
        context.load_cert_chain(args.cert, args.key)
    body = Path(args.body_file).read_bytes() if args.body_file else None
    require(body is None or len(body) <= 1048576, "synthetic request body exceeds bound")
    if workload_kind == 'memory_query':
        require(parsed.path in ('/api/v1/search/find', '/api/v1/search/search') and body is not None,
                'memory_query requires a memory search POST')
        query = json.loads(body)
        require(isinstance(query, dict) and isinstance(query.get('query'), str) and query['query'].strip(),
                'memory_query requires a nonempty query')
    key = protected_secret(args.api_key_file) if getattr(args, "api_key_file", None) else os.environ.get(args.api_key_env) if args.api_key_env else None
    require(not args.api_key_env or key, "missing API key environment reference")
    rows, errors, guard = [], [], threading.Lock()
    local, sessions = threading.local(), []
    def sample(number, phase, end):
        if not hasattr(local, 'session'):
            with guard:
                local.session = ConnectionSession('w%d' % len(sessions)); sessions.append(local.session)
        return request(args.url, context, args.server_id, body, key, args.timeout, args.run_id, number, phase,
                       end, prefix, local.session, connection_mode, workload_kind)
    slots = threading.BoundedSemaphore(args.concurrency)
    def done(future):
        try:
            row = future.result()
            with guard:
                write_measurement(stream, row)
                rows.append(row)
        except Exception as exc:
            with guard:
                errors.append(type(exc).__name__)
        finally:
            slots.release()
    barrier = getattr(args, "start_barrier", None)
    if barrier is not None:
        barrier.wait(timeout=30)
    start, number = time.monotonic(), 0
    started_at_ms = time.time_ns() // 1000000
    end = start + args.warmup + args.measure
    try:
        with measurement_log(directory / "requests.jsonl") as stream, ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            while time.monotonic() < end:
                scheduled = start + number / args.rate
                if scheduled >= end:
                    time.sleep(max(0, end - time.monotonic()))
                    break
                pause = scheduled - time.monotonic()
                if pause > 0:
                    time.sleep(min(pause, max(0, end - time.monotonic())))
                now = time.monotonic()
                if now >= end:
                    break
                phase = "warmup" if scheduled < start + args.warmup else "measurement"
                if slots.acquire(blocking=False):
                    f = pool.submit(sample, number, phase, end)
                    f.add_done_callback(done)
                else:
                    row = {"run_id": args.run_id, "request_id": "%s-%d" % (prefix, number), "phase": phase,
                           "started_at_ms": time.time_ns() // 1000000, "outcome": "overload",
                           'connection_mode': connection_mode, 'workload_kind': workload_kind}
                    with guard:
                        rows.append(row); write_measurement(stream, row)
                number += 1
    finally:
        for session in sessions: session.close('run_finished')
    complete = not errors and len(rows) == number
    result = dict(summarize_requests(rows, args.measure), schema="argus.load.v1", run_id=args.run_id,
                  measurement_seconds=args.measure, warmup_seconds=args.warmup, rate=args.rate,
                  measurement_started_at_ms=started_at_ms + args.warmup * 1000,
                  measurement_ended_at_ms=started_at_ms + (args.warmup + args.measure) * 1000,
                  max_concurrency=args.concurrency, connection_mode=connection_mode, workload_kind=workload_kind,
                  server_id=args.server_id,
                  connections=summarize_connections(rows),
                  measurement_complete=complete, scheduled_requests=number, collector_error_classes=sorted(set(errors)),
                  requests_sha256=sha(directory / 'requests.jsonl'),
                  result="PASS" if complete and any(r.get("phase") == "measurement" and r.get("outcome") == "success" for r in rows) else "UNKNOWN")
    if workload_kind == 'memory_query':
        measured = [r for r in rows if r.get('phase') == 'measurement']
        result['memory_results'] = {kind: sum(r.get('memory_result') == kind for r in measured)
                                    for kind in ('nonempty', 'empty', 'unknown')}
        result['memory_nonempty_goodput_rps'] = sum(r.get('outcome') == 'success' and r.get('memory_result') == 'nonempty'
                                                  for r in measured) / args.measure
        if result['memory_results']['empty']:
            result['result'] = 'FAIL'
        elif result['memory_results']['unknown'] or not result['memory_results']['nonempty']:
            result['result'] = 'UNKNOWN'
    atomic(directory / "load-result.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ("url", "server-id", "output", "run-id"):
        p.add_argument("--" + arg, required=True)
    for arg in ('cert', 'key', 'bundle', 'credentials-dir', 'client-id'):
        p.add_argument('--' + arg)
    p.add_argument("--api-key-env"); p.add_argument("--body-file")
    p.add_argument("--request-prefix", help="unique per-client wire request prefix; run ID remains shared")
    p.add_argument("--api-key-file", help="protected user-key file; never its content")
    p.add_argument('--connection-mode', choices=('new', 'reuse'), default='new', help='new TLS per call or one live connection per worker')
    p.add_argument('--workload-kind', choices=('status_api', 'memory_query', 'custom_api'),
                   help='memory_query requires an actual nonempty private-memory search; status API is separate')
    p.add_argument("--rate", type=float, required=True)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--warmup", type=float, default=30)
    p.add_argument("--measure", type=float, default=120)
    p.add_argument("--timeout", type=float, default=10)
    print(json.dumps(run(p.parse_args()), indent=2))


if __name__ == "__main__":
    main()
