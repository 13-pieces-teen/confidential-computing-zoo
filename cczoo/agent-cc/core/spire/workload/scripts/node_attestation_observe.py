#!/usr/bin/env python3
"""Read-only Node enrollment/renewal observation; never erase Agent credentials.

Snapshot the Server's public Agent record and the selected Agent's dedicated
Prometheus endpoint. Missing metrics are UNKNOWN, never an implicit zero.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def spiffe_id(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        domain = value.get("trust_domain", value.get("trustDomain"))
        return "spiffe://" + domain + value.get("path", "") if domain else None
    return None


def field(record, *names):
    for name in names:
        if name in record:
            return record[name]
    return None


def agent_record(value, identity):
    agents = value.get("agents", []) if isinstance(value, dict) else value
    if not isinstance(agents, list):
        raise ValueError("SPIRE agent list JSON must contain an agents array")
    found = [agent for agent in agents if spiffe_id(agent.get("id")) == identity]
    if len(found) > 1:
        raise ValueError("multiple matching Agent records")
    if not found:
        return {"present": False}
    agent = found[0]
    return {"present": True,
            "serial": field(agent, "x509_svid_serial_number", "x509SvidSerialNumber", "x509svid_serial_number"),
            "expires_at": field(agent, "x509_svid_expires_at", "x509SvidExpiresAt", "x509svid_expires_at"),
            "attestation_type": field(agent, "attestation_type", "attestationType"),
            "banned": agent.get("banned", False),
            "can_reattest": field(agent, "can_reattest", "canReattest")}


def metric_sum(text, name):
    values = []
    for line in text.splitlines():
        match = re.fullmatch(re.escape(name) + r'(?:\{[^\n]*\})?\s+([0-9.eE+\-]+)(?:\s+\d+)?', line.strip())
        if match:
            number = float(match[1])
            if number < 0 or number == float("inf") or number != number:
                raise ValueError("nonfinite or negative metric")
            values.append(number)
    return sum(values) if values else None


def snapshot(args):
    started = time.time_ns() // 1_000_000
    result = subprocess.run([args.spire_server, "agent", "list", "-socketPath", args.server_socket,
                             "-output", "json"], capture_output=True, text=True, timeout=15, check=True)
    record = agent_record(json.loads(result.stdout), args.agent_id)
    agent_observed_at = time.time_ns() // 1_000_000
    opener = build_opener(NoRedirect(), ProxyHandler({}))
    raw, metrics_error = b"", None
    try:
        with opener.open(Request(args.metrics_url, headers={"Accept": "text/plain"}), timeout=5) as response:
            raw = response.read(4_194_305)
    except OSError as error:
        # Before first startup the endpoint may not exist. Preserve that fact,
        # not a fabricated zero counter, alongside the public absent record.
        metrics_error = type(error).__name__
    if len(raw) > 4_194_304:
        raise ValueError("metrics response exceeds 4 MiB")
    text = raw.decode("utf-8")
    data = {"version": 1, "agent_id": args.agent_id, "started_at_ms": started,
            "completed_at_ms": time.time_ns() // 1_000_000, "agent": record,
            "agent_observed_at_ms": agent_observed_at, "metrics_error": metrics_error,
            "metrics_url": args.metrics_url, "metrics_sha256": hashlib.sha256(raw).hexdigest(),
            "metric_names": {"attempts": args.attempt_metric, "quote_samples": args.quote_count_metric,
                             "process_start": args.process_start_metric},
            "attempts": metric_sum(text, args.attempt_metric),
            "quote_samples": metric_sum(text, args.quote_count_metric),
            "process_start": metric_sum(text, args.process_start_metric)}
    with Path(args.output).open("x", encoding="utf-8") as output:
        output.write(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))


def assess(before, after, mode, clock_uncertainty_ms=0):
    result = {"mode": mode, "result": "UNKNOWN", "scope": "two explicit public-record/metrics snapshots; no workload or continuous-integrity claim"}
    if before.get("agent_id") != after.get("agent_id") or before.get("metrics_url") != after.get("metrics_url") or before.get("metric_names") != after.get("metric_names"):
        return result | {"reason": "snapshots do not describe the same Agent and metric sources"}
    if before["completed_at_ms"] >= after["started_at_ms"]:
        return result | {"reason": "snapshots overlap or are out of order"}
    old, new = before["agent"], after["agent"]
    if not new.get("present") or not new.get("serial") or new.get("attestation_type") != "argus_tdx" or new.get("banned"):
        return result | {"reason": "no valid argus_tdx public Agent record after observation"}
    if any(after.get(name) is None for name in ("attempts", "quote_samples", "process_start")):
        return result | {"reason": "required Node-specific metrics or process identity are unavailable after observation"}
    try:
        if int(new["expires_at"]) * 1000 <= after["completed_at_ms"]:
            return result | {"reason": "Agent SVID expiry is not after the observation"}
    except (TypeError, ValueError, KeyError):
        return result | {"reason": "Agent SVID expiry unavailable"}
    if mode == "enrollment":
        if old.get("present"):
            return result | {"reason": "Agent was already registered; this does not demonstrate initial enrollment"}
        process_start_ms = after["process_start"] * 1000
        # This branch handles a genuinely new process and an unavailable prior
        # metrics endpoint. Its counters are observations from that process,
        # not deltas obtained by assuming missing old values were zero.
        fresh = (before.get("agent_observed_at_ms", float("inf")) <= process_start_ms - clock_uncertainty_ms and
                 process_start_ms + clock_uncertainty_ms <= after.get("agent_observed_at_ms", 0))
        if fresh and after["attempts"] > 0 and after["quote_samples"] > 0:
            return result | {"result": "PASS", "node_protocol_attempts_in_new_process": after["attempts"],
                             "node_quote_samples_in_new_process": after["quote_samples"],
                             "reason": "absent Agent registered and a new dedicated Agent metrics process in the observation window produced Node proof evidence; not a historical first-ever claim"}
    if any(before.get(name) is None for name in ("attempts", "quote_samples", "process_start")):
        return result | {"reason": "prior metrics unavailable and no new Agent process was proved inside the observation window"}
    if before["process_start"] != after["process_start"]:
        return result | {"reason": "Agent metrics process restarted; counter continuity is unknown"}
    attempts = after["attempts"] - before["attempts"]
    quotes = after["quote_samples"] - before["quote_samples"]
    result.update(node_protocol_attempts=attempts, node_quote_samples=quotes)
    if attempts < 0 or quotes < 0:
        return result | {"reason": "metric counter reset"}
    if mode == "enrollment":
        if attempts <= 0 or quotes <= 0:
            return result | {"reason": "registration observed without correlated Node protocol/Quote observations"}
        return result | {"result": "PASS", "reason": "previously absent Agent registered with Node proof observations; not a historical first-ever claim"}
    if not old.get("present") or not old.get("serial") or old["serial"] == new["serial"]:
        return result | {"reason": "ordinary Agent SVID serial renewal was not observed"}
    try:
        if int(old["expires_at"]) * 1000 <= before["completed_at_ms"]:
            return result | {"reason": "initial Agent credential had already expired"}
    except (TypeError, ValueError, KeyError):
        return result | {"reason": "initial Agent SVID expiry unavailable"}
    if attempts or quotes:
        return result | {"result": "FAIL", "reason": "renewal interval also invoked the Node attestation protocol or produced a Node Quote"}
    return result | {"result": "PASS", "reason": "Agent serial changed with continuous counters and no observed Node protocol/Quote increment"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    p = commands.add_parser("snapshot")
    for name in ("spire-server", "server-socket", "agent-id", "metrics-url", "attempt-metric", "quote-count-metric", "process-start-metric", "output"):
        p.add_argument("--" + name, required=True)
    p = commands.add_parser("check")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--mode", choices=("enrollment", "renewal"), required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--clock-uncertainty-ms", type=int, required=True)
    args = parser.parse_args()
    if args.action == "snapshot":
        snapshot(args)
    else:
        if args.clock_uncertainty_ms < 0:
            parser.error("clock uncertainty must be nonnegative")
        result = assess(json.loads(Path(args.before).read_text()), json.loads(Path(args.after).read_text()), args.mode, args.clock_uncertainty_ms)
        with Path(args.output).open("x", encoding="utf-8") as output:
            output.write(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["result"] == "PASS" else 1 if result["result"] == "FAIL" else 2)


if __name__ == "__main__":
    main()
