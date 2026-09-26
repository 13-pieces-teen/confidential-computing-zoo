#!/usr/bin/env python3
"""Bind existing tool evidence to a runner invocation without replacing its state.

The native business result is also its resume journal; only the separate envelope
is rewritten. Result bodies and keys are not copied into this envelope.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from common import atomic, read, require, sha, protected_secret, run_logged

ROOT = Path(__file__).resolve().parents[2]


def execute(args):
    run_id, operation_id = os.environ.get("ARGUS_RUN_ID"), os.environ.get("ARGUS_OPERATION_ID")
    require(run_id and operation_id, "step must run inside the experiment runner")
    require(not Path(args.receipt).is_absolute() and ".." not in Path(args.receipt).parts,
            "receipt must stay in run directory")
    directory = Path(args.output).resolve(); directory.mkdir(parents=True, exist_ok=True)
    native = directory / {"fleet": "business", "load": "load", "locomo": "locomo"}[args.tool]
    if args.tool == "fleet":
        script = ROOT / "adapters/OpenClaw/spiffe_client/fleet_business.py"
        argv = [sys.executable, str(script), args.action, "--config", args.config, "--output", str(native)]
        result_file = native / "result.json"
        if args.instance: argv += ["--instance", args.instance]
    elif args.tool == "locomo":
        require(args.action in ('run', 'resume'), 'LoCoMo supports run or resume')
        argv = [sys.executable, str(Path(__file__).with_name('locomo_run.py')), args.action,
                '--config', args.config, '--output', str(native)]
        result_file = native / 'result.json'
    else:
        require(args.action == "run", "load trials cannot resume midway; retain interrupted trial and prepare a new run")
        argv = [sys.executable, str(Path(__file__).with_name("load_fleet.py")), "--config", args.config,
                "--output", str(native), "--run-id", run_id, "--clients", str(args.clients)]
        result_file = native / "load-result.json"
    code = None
    secrets = []
    for instance in read(args.config).get("instances", []):
        if instance.get("api_key_file"):
            try:
                secrets.append(protected_secret(instance["api_key_file"]))
            except (OSError, ValueError):
                pass  # The owning business/load command reports configuration errors.
    try:
        code = run_logged(argv, diagnostic=directory / (args.tool + "-diagnostic.json"), stage=args.tool,
                          timeout=args.timeout, secrets=secrets).returncode
    except (subprocess.TimeoutExpired, OSError):
        pass
    verdict, source_hash, measurement_complete, native_result = "UNKNOWN", None, False, None
    if result_file.is_file():
        evidence = read(result_file)
        native_result = evidence.get('result')
        source_hash = sha(result_file)
        measurement_complete = evidence.get("measurement_complete") is True
        native_complete = args.tool != 'fleet' or (evidence.get('completed') is True and evidence.get('operation_id') == operation_id)
        if native_complete and evidence.get("run_id") == run_id and evidence.get("result") in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN"):
            verdict = evidence["result"]
        if args.tool == 'locomo':
            verdict = ('PASS' if evidence.get('run_id') == run_id and evidence.get('operation_id') == operation_id
                       and evidence.get('result') == 'COMPLETE' else 'UNKNOWN')
        if code is None or (code != 0 and verdict == "PASS"):
            verdict = "UNKNOWN"
    envelope = {"schema": "argus.step.v1", "run_id": run_id, "operation_id": operation_id,
                "attempt": int(os.environ.get("ARGUS_ATTEMPT", "1")),
                "measurement_complete": measurement_complete if args.tool == "load" else None,
                "result": verdict, "tool": args.tool, "exit_code": code,
                "native_result": native_result,
                "evidence_scope": ('application_workload_completion_not_security' if args.tool == 'locomo' else args.tool),
                "source": Path(os.path.relpath(result_file, (directory / args.receipt).parent)).as_posix(),
                "source_sha256": source_hash}
    atomic(directory / args.receipt, envelope)
    return 0 if verdict == "PASS" else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("tool", choices=("fleet", "load", "locomo")); p.add_argument("action", choices=("run", "resume", "isolation"))
    p.add_argument("--config", required=True); p.add_argument("--output", required=True)
    p.add_argument("--receipt", default="step-result.json"); p.add_argument("--instance")
    p.add_argument("--clients", type=int, default=1); p.add_argument("--timeout", type=float, default=15000)
    a = p.parse_args()
    require(not Path(a.receipt).is_absolute() and ".." not in Path(a.receipt).parts, "receipt must stay in run directory")
    return execute(a)


if __name__ == "__main__": sys.exit(main())
