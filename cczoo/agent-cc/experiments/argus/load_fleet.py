#!/usr/bin/env python3
"""Concurrent independent-client API load using distinct mTLS and user keys."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import hashlib
from types import SimpleNamespace
import threading
import os

from common import measurement_log, write_measurement, atomic, read, require, protected_secret, sha
from analysis import summarize_requests
import load
from client_material import certificate_identity, certificate_public_key, snapshot


def execute(config, output, run_id, clients):
    c, output = read(config), Path(output)
    require(type(clients) is int and clients > 0, "positive independent client count required")
    require(c.get("frozen") is True and c["rate"] > 0, "frozen pilot arrival load required")
    require(c.get('connection_mode', 'new') in ('new', 'reuse'), 'connection_mode must be new or reuse')
    require(not output.exists(), "fresh load directory required")
    if clients > len(c['instances']):
        output.mkdir(parents=True)
        (output / 'requests.jsonl').write_text('', encoding='utf-8')
        result = {'schema':'argus.load-fleet.v1', 'run_id':run_id, 'result':'NOT_RUN', 'reason':'CAPACITY_STOP',
                  'requested_clients':clients, 'provisioned_clients':len(c['instances']),
                  'measurement_complete':False, 'requests_sha256':sha(output / 'requests.jsonl'),
                  'measurement_seconds':c.get('measurement_seconds', 120)}
        atomic(output / 'load-result.json', result)
        return result
    selected = c["instances"][:clients]
    for item in selected:
        require(isinstance(item.get("name"), str) and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", item["name"]) is not None,
                "client name must be a safe single directory component")
        require(set(item) <= {"name", "url", "cert", "key", "bundle", "server_id", "api_key_file", "body_file", 'credentials_dir', 'client_id'},
                "unknown client load configuration")
    materials = [snapshot(item['credentials_dir']) if item.get('credentials_dir') else item for item in selected]
    for key in ("name", "api_key_file"):
        require(len({item[key] for item in selected}) == clients, "independent client " + key + " required")
    for key in ('cert', 'key'):
        require(len({item[key] for item in materials}) == clients, 'independent client ' + key + ' required')
    identities = [certificate_identity(item['cert']) for item in materials]
    require(len(set(identities)) == clients, 'distinct actual SPIFFE identities required')
    for item, identity in zip(selected, identities):
        require(not item.get('client_id') or item['client_id'] == identity, 'client identity differs from load configuration')
    require(len({certificate_public_key(item['cert']) for item in materials}) == clients, 'distinct actual client key pairs required')
    require(len({hashlib.sha256(protected_secret(item['api_key_file']).encode()).digest() for item in selected}) == clients,
            'distinct actual business keys required')
    output.mkdir(parents=True)
    attempt = int(os.environ.get("ARGUS_ATTEMPT", "1"))
    start_barrier = threading.Barrier(clients)
    def sample(item):
        args = dict(item, run_id=run_id, request_prefix=run_id + "-a" + str(attempt) + "-" + item["name"], output=str(output / item["name"]), rate=c["rate"] / clients,
                    concurrency=c.get("concurrency_per_client", 8), warmup=c.get("warmup_seconds", 30),
                    measure=c.get("measurement_seconds", 120), timeout=c.get("timeout_seconds", 10), api_key_env=None,
                    start_barrier=start_barrier, connection_mode=c.get('connection_mode', 'new'),
                    workload_kind=c.get('workload_kind'))
        args.pop("name")
        args.setdefault("body_file", c.get("body_file"))
        return load.run(SimpleNamespace(**args))
    with ThreadPoolExecutor(max_workers=clients) as pool:
        results = list(pool.map(sample, selected))
    rows = []
    for item in selected:
        source = output / item["name"] / "requests.jsonl"
        if source.is_file():
            for line in source.read_text(encoding="utf-8").splitlines():
                row = json.loads(line); row["instance_id"] = item["name"]
                rows.append(row)
    with measurement_log(output / "requests.jsonl") as stream:
        for row in sorted(rows, key=lambda r: (r["started_at_ms"], r["request_id"])):
            write_measurement(stream, row)
    result = dict(summarize_requests(rows, c.get("measurement_seconds", 120)), schema="argus.load-fleet.v1",
                  run_id=run_id, attempt=attempt, clients=clients, per_client=results, measurement_seconds=c.get("measurement_seconds", 120),
                  connection_mode=c.get('connection_mode', 'new'), connections=load.summarize_connections(rows),
                  server_ids=sorted({item['server_id'] for item in selected}),
                  measurement_started_at_ms=max(r['measurement_started_at_ms'] for r in results),
                  measurement_ended_at_ms=min(r['measurement_ended_at_ms'] for r in results),
                  workload_kind=results[0]['workload_kind'] if len({r['workload_kind'] for r in results}) == 1 else 'mixed_api',
                  measurement_complete=all(r.get('measurement_complete') is True for r in results),
                  requests_sha256=sha(output / 'requests.jsonl'),
                  result='FAIL' if any(r['result'] == 'FAIL' for r in results) else "PASS" if all(r["result"] == "PASS" for r in results) else "UNKNOWN")
    if result['workload_kind'] == 'memory_query':
        result['memory_results'] = {kind: sum(r['memory_results'][kind] for r in results) for kind in ('nonempty', 'empty', 'unknown')}
        result['memory_nonempty_goodput_rps'] = sum(r['memory_nonempty_goodput_rps'] for r in results)
    atomic(output / "load-result.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "output", "run-id"): p.add_argument("--" + name, required=True)
    p.add_argument("--clients", type=int, required=True)
    a = p.parse_args(); result = execute(a.config, a.output, a.run_id, a.clients)
    print(json.dumps(result, indent=2)); return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
