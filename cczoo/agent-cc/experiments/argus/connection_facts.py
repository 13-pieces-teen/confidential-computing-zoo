#!/usr/bin/env python3
"""Prepare independent synthetic bodies for the three E2 connection lanes."""
import argparse
import hashlib
import json
from pathlib import Path
import random

from common import atomic, require, sha
from fact_protocol import encode_fact, parse_fact


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
            facts.append(dict(item, lane=lane, index=index))
    atomic(output / "payload-plan.json", plan)
    atomic(output / "facts.json", {"schema": "argus.connection-facts.v1", "facts": facts})
    return {"payload_plan": str(output / "payload-plan.json"), "facts": len(facts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--requests", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.seed, args.requests)))


if __name__ == "__main__":
    main()
