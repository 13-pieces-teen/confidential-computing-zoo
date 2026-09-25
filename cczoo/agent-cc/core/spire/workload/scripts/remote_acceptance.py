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


def json_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def fault_checkpoint(path):
    """Recover a durable checkpoint if interruption truncated only the last line.

    Complete lines must always parse. A malformed terminated last line is
    corruption, not an interrupted write. Probe/receiver files stay strict.
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
        emit({"type": "probe_start", "run_id": args.run_id, "at_ms": now_ms(),
              "interval_ms": int(args.interval * 1000), "timeout_ms": int(args.timeout * 1000),
              "method": args.method, "server_id": args.server_id, "body_sha256": hashlib.sha256(body).hexdigest()})
        def lane(name):
            original = factory(True) if name == "existing" else None
            while time.monotonic() < deadline:
                conn = original if original else factory(False)
                row = {"type": "request", "run_id": args.run_id, "request_id": str(uuid.uuid4()),
                       "lane": name, "started_at_ms": now_ms(), "ok": False, "response_bytes": 0,
                       "sentinel_response": False, "request_body_bytes": len(body)}
                headers = {"X-Argus-Run-ID": args.run_id, "X-Argus-Request-ID": row["request_id"],
                           "Content-Type": args.content_type, "Connection": "keep-alive"}
                if api_key:
                    headers["X-API-Key"] = api_key
                try:
                    conn.request(args.method, path, body=body if body else None, headers=headers)
                    response = conn.getresponse()
                    content = response.read(1_048_577)
                    row.update(http_status=response.status, response_bytes=len(content),
                               ok=200 <= response.status < 300,
                               sentinel_response=bool(marker and marker in content), server_serial=conn.serial)
                    if len(content) > 1_048_576:
                        row.update(ok=False, error="response exceeded the 1 MiB observation limit", incomplete=True)
                        conn.close()
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
                lane(name)
            except Exception as error:
                with lock:
                    errors.append({"lane": name, "error": type(error).__name__})
        threads = [threading.Thread(target=guarded_lane, args=(name,)) for name in ("existing", "new")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        emit({"type": "probe_stop", "run_id": args.run_id, "at_ms": now_ms(), "complete": not errors, "errors": errors})
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
                  "target": target, "started_at_ms": now_ms(), "executed": False}
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
        checkpoint()
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
        record.update(completed_at_ms=now_ms(), executed=result.returncode == 0, exit_code=result.returncode)
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


def assess(rows, event, receiver=None, *, bound_ms, clock_uncertainty_ms, min_samples=3, max_gap_ms=3000):
    run = event.get("run_id")
    result = {"run_id": run, "result": "UNKNOWN", "traffic": "UNKNOWN", "receiver_delivery": "NOT_RUN",
              "bound_ms": bound_ms, "clock_uncertainty_ms": clock_uncertainty_ms,
              "scope": "requests started after the stop bound; excludes already-running streams and recovery"}
    if not event.get("executed") or not isinstance(event.get("started_at_ms"), int):
        return result | {"reason": "no successful fault command record"}
    fault_at = event["started_at_ms"]
    cutoff = fault_at + bound_ms + clock_uncertainty_ms
    samples = [row for row in rows if row.get("type") == "request" and row.get("run_id") == run]
    starts = [row for row in rows if row.get("type") == "probe_start" and row.get("run_id") == run]
    stops = [row for row in rows if row.get("type") == "probe_stop" and row.get("run_id") == run]
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
    else:
        result["traffic"] = "PASS"
    if receiver is None:
        return result | {"reason": result.get("reason", "independent receiver evidence not supplied")}
    logs = [row for row in receiver if row.get("run_id") == run]
    starts = [row for row in logs if row.get("type") == "receiver_start"]
    stops = [row for row in logs if row.get("type") == "receiver_stop" and row.get("complete") is True]
    receipts = [row for row in logs if row.get("type") == "received"]
    late_ids = {row["request_id"] for row in late}
    delivered = [row for row in receipts if row.get("request_id") in late_ids]
    violation = [row for row in delivered if held or any(sample["request_id"] == row.get("request_id") and sample["lane"] == "existing" for sample in late)]
    if violation:
        result.update(result="FAIL", receiver_delivery="FAIL", delivered_requests=len(violation),
                      received_body_bytes=sum(row.get("received_body_bytes", 0) for row in violation),
                      reason="the independent application receiver recorded post-bound requests")
        return result
    if not held:
        return result | {"receiver_delivery": "UNKNOWN", "reason": result.get("reason", "re-admission boundary not independently established")}
    covered = (len(starts) == len(stops) == 1 and
               starts[0].get("at_ms", float("inf")) <= min(row["started_at_ms"] for row in samples) - clock_uncertainty_ms and
               stops[0].get("at_ms", 0) >= max(row["completed_at_ms"] for row in samples) + clock_uncertainty_ms)
    receipt_ids = {row.get("request_id") for row in receipts}
    baseline_ids = {row["request_id"] for row in samples if row["completed_at_ms"] < fault_at - clock_uncertainty_ms and row["ok"]}
    if not covered or not baseline_ids.issubset(receipt_ids):
        result.update(receiver_delivery="UNKNOWN", reason=result.get("reason", "receiver coverage or healthy-baseline receipts are incomplete"))
        return result
    result["receiver_delivery"] = "PASS"
    if result["traffic"] == "PASS":
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
        result = assess(json_rows(args.trace), fault_checkpoint(args.fault), json_rows(args.receiver) if args.receiver else None,
                        bound_ms=args.bound_ms, clock_uncertainty_ms=args.clock_uncertainty_ms, max_gap_ms=args.max_gap_ms)
        save(args.output, result)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["result"] == "PASS" else 1 if result["result"] == "FAIL" else 2)


if __name__ == "__main__":
    main()
