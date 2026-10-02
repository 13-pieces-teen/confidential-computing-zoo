#!/usr/bin/env python3
"""Prepare independent synthetic bodies for the three E2 connection lanes."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import sys
import threading

from common import atomic, require, sha
from fact_protocol import encode_fact, parse_fact, facts_in


def prepare(output, seed, requests=200):
    output = Path(output)
    require(not output.exists() and 3 <= requests <= 10000, "fresh output and at least three requests per lane required")
    output.mkdir(parents=True, mode=0o700)
    rng = random.Random(seed)
    plan = {"schema": "argus.connection-payloads.v1", "seed": seed,
            "scope": "synthetic connection microexperiment, not Agent task correctness"}
    facts = []
    for lane in ("existing", "new", "inflight"):
        plan[lane] = []
        for index in range(1 if lane == "inflight" else requests):
            fields = {"fact_id": f"{rng.getrandbits(128):032x}", "project_id": f"{rng.getrandbits(48):012x}",
                      "chain_id": f"{rng.getrandbits(48):012x}", "event_ref": f"{rng.getrandbits(128):032x}",
                      "amount_cents": rng.randrange(1, 100000000)}
            frame = encode_fact(fields)
            # Keep the unique full fact near the end of the in-flight body so
            # first-read-triggered faults precede completion of that new fact.
            query = ("p" * 8192 + " " if lane == "inflight" else "") + frame
            path = output / f"{lane}-{index:05d}.json"
            path.write_text(json.dumps({"query": query, "limit": 1}, separators=(",", ":")), encoding="utf-8")
            path.chmod(0o600)
            value = parse_fact(frame)
            item = {k: value[k] for k in ("fact_id", "full_fact_sha256", "fact_bytes")}
            plan[lane].append(dict(item, body_file=path.name, body_sha256=sha(path)))
            facts.append(dict(item, lane=lane, index=index, client_id=lane, step_id=f"{lane}-{index:05d}",
                              task_id=f"{lane}-{index:05d}"))
    atomic(output / "payload-plan.json", plan)
    atomic(output / "facts.json", {"schema": "argus.connection-facts.v1", "facts": facts})
    return {"payload_plan": str(output / "payload-plan.json"), "facts": len(facts)}


def measurement_inputs(probe, traces, run_id):
    """Adapt the default E2 lanes to the fact assessor without inventing releases."""
    if probe.get("payload_plan"):
        path = Path(probe["payload_plan"])
        plan = json.loads(path.read_text(encoding="utf-8"))
        require(plan.get("schema") == "argus.connection-payloads.v1", "unsupported payload plan")
        facts = [dict(item, client_id=lane, step_id=f"{lane}-{index:05d}", task_id=f"{lane}-{index:05d}")
                 for lane in ("existing", "new", "inflight") for index, item in enumerate(plan[lane])]
        for fact in facts:
            body = path.parent / fact["body_file"]
            require(sha(body) == fact["body_sha256"], "payload bytes differ from plan")
            actual = facts_in(body.read_text(encoding="utf-8"))
            require(len(actual) == 1 and all(actual[0][key] == fact[key]
                    for key in ("fact_id", "full_fact_sha256", "fact_bytes")), "payload fact differs from plan")
    else:
        raw = Path(probe["body_file"]).read_text(encoding="utf-8")
        facts = [dict(item, client_id="shared", step_id=item["fact_id"], task_id=item["fact_id"])
                 for item in facts_in(raw)]
    starts = [r for r in traces if r.get("type") == "probe_start" and r.get("run_id") == run_id]
    stops = [r for r in traces if r.get("type") == "probe_stop" and r.get("run_id") == run_id]
    require(len(starts) == 1, "one probe start required")
    requests = [r for r in traces if r.get("type") in ("request", "stream_start") and r.get("run_id") == run_id]
    steps = []
    for fact in facts:
        matches = [r for r in requests if r.get("fact_id") == fact["fact_id"] or not probe.get("payload_plan")]
        steps.append(dict(fact, request_ids=sorted({r["request_id"] for r in matches if r.get("request_id")}),
                          task_result="NOT_RUN"))
    result = {"schema": "argus.connection-fact-result.v1", "run_id": run_id,
              "started_at_ms": starts[0]["at_ms"], "steps": steps}
    if len(stops) == 1 and stops[0].get("complete") is True:
        result["completed_at_ms"] = stops[0]["at_ms"]
    return {"schema": "argus.connection-facts.v1", "facts": facts}, result


class ReleaseObserver:
    """Bounded prefix matching at actual transport writes; never receiver evidence."""
    prefix = re.compile(rb"ARGUS_FACT_V1\|id=([0-9a-f]{32})")

    def __init__(self, facts, emit):
        self.facts, self.emit = {f["fact_id"]: f for f in facts}, emit
        self.tail, self.spans, self.seen = b"", [], set()

    def feed(self, data, started, completed, request_id, status, socket_id=None):
        combined = self.tail + data
        spans = self.spans + [(len(data), started, completed, status)]
        matched = False
        for match in self.prefix.finditer(combined):
            fact_id = match[1].decode("ascii")
            if fact_id not in self.facts or fact_id in self.seen:
                continue
            matched = True
            offset, first, last, states = 0, None, None, []
            for size, begin, end, state in spans:
                if offset <= match.start() < offset + size:
                    first, last = begin, end
                if offset < match.end() and offset + size > match.start():
                    states.append(state)
                offset += size
            self.seen.add(fact_id)
            self.emit({"type": "input_release", "source": "client_tls_socket_write",
                       "boundary": "client_fact_prefix_transport_write", "request_id": request_id,
                       "socket_id": socket_id, **{k: self.facts[fact_id][k] for k in ("fact_id", "full_fact_sha256", "fact_bytes")},
                       "status": "OBSERVED" if states and all(s == "OBSERVED" for s in states) else "UNKNOWN",
                       **first, "completed_at_ms": last["at_ms"], "completed_monotonic_ns": last.get("monotonic_ns")})
        # A prefix can straddle two writes. Keep its original write clock so
        # recognition in the next chunk does not shift release to a later time.
        retain = min(64, len(combined))
        self.tail = combined[-retain:]
        trim, kept = len(combined) - retain, []
        for size, begin, end, state in spans:
            removed = min(trim, size)
            trim -= removed
            if size > removed:
                kept.append((size - removed, begin, end, state))
        self.spans = kept
        if status != "OBSERVED" and not matched and not self.seen:
            self.emit({"type": "release_gap", "request_id": request_id,
                       "source": "client_tls_socket_write", "boundary": "client_fact_prefix_transport_write",
                       **started, "completed_at_ms": completed["at_ms"], "completed_monotonic_ns": completed.get("monotonic_ns"),
                       "reason": "socket write raised; an unassociated fact fragment may have been released"})


def observed_connection_class(base, facts, emit, clocks):
    """Subclass only the existing probe's send boundary; TLS/protocol stay intact."""
    class ObservedConnection(base):
        def putrequest(self, *args, **kwargs):
            self.release_observer = ReleaseObserver(facts, emit)
            self.release_request_id, self.release_headers_pending, self.release_chunked = None, True, False
            return super().putrequest(*args, **kwargs)

        def putheader(self, name, *values):
            if name.lower() == "x-argus-request-id":
                self.release_request_id = str(values[0])
            if name.lower() == "transfer-encoding":
                self.release_chunked = str(values[0]).lower() == "chunked"
            return super().putheader(name, *values)

        def send(self, data):
            payload = data
            if self.release_headers_pending:
                require(isinstance(data, bytes) and b"\r\n\r\n" in data, "probe header boundary changed")
                payload = data.split(b"\r\n\r\n", 1)[1]
                self.release_headers_pending = False
            elif self.release_chunked:
                prefix, payload = data.split(b"\r\n", 1)
                size = int(prefix, 16)
                require(len(payload) == size + 2 and payload.endswith(b"\r\n"), "probe chunk boundary changed")
                payload = payload[:size]
            started = clocks()
            try:
                value = super().send(data)
            except BaseException:
                if payload:
                    self.release_observer.feed(payload, started, clocks(), self.release_request_id, "UNKNOWN", getattr(self, "socket_id", None))
                raise
            if payload:
                self.release_observer.feed(payload, started, clocks(), self.release_request_id, "OBSERVED", getattr(self, "socket_id", None))
            return value
    return ObservedConnection


def observed_probe_main(argv):
    # Reuse the existing parser, lane scheduling, pinning and transport behavior.
    # This runs in the coordinator's finite child, not in the production service.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core/spire/workload/scripts"))
    import remote_acceptance as acceptance
    output = Path(argv[argv.index("--output") + 1])
    run_id = argv[argv.index("--run-id") + 1]
    probe = {key: argv[argv.index("--" + key.replace("_", "-")) + 1]
             for key in ("payload_plan", "body_file") if "--" + key.replace("_", "-") in argv}
    # Only the static fact identities are used here; no generator/plan timestamp
    # becomes a release receipt.
    fake_start = [{"type": "probe_start", "run_id": run_id, "at_ms": 0}]
    manifest, _ = measurement_inputs(probe, fake_start, run_id)
    guard, sequence = threading.Lock(), 0
    with output.with_name("releases.jsonl").open("x", encoding="utf-8") as stream:
        def emit(row):
            nonlocal sequence
            with guard:
                sequence += 1
                stream.write(json.dumps(dict(row, run_id=run_id, record_seq=sequence)) + "\n")
                stream.flush()
        emit({"type": "release_start", "schema": "argus.input-release.v1",
              "boundary": "client_fact_prefix_transport_write", **acceptance.clocks()})
        original_class, original_argv = acceptance.PinnedConnection, sys.argv
        acceptance.PinnedConnection = observed_connection_class(original_class, manifest["facts"], emit, acceptance.clocks)
        sys.argv = [__file__, "probe", *argv]
        complete = False
        try:
            acceptance.main()
            complete = True
        finally:
            emit({"type": "release_stop", "complete": complete, **acceptance.clocks()})
            acceptance.PinnedConnection, sys.argv = original_class, original_argv


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        return observed_probe_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--requests", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.seed, args.requests)))


if __name__ == "__main__":
    main()
