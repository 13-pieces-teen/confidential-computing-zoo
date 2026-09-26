#!/usr/bin/env python3
"""Persistent host-local experiment runner; invoke on each host with its role.

Commands are argv arrays, never shell fragments. An interrupted mutation is not
replayed. Recovery is a separately declared query of an already known operation.
Exit status alone never establishes a paper result.
"""
import argparse
import datetime
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time

from common import GROUPS, SAFE, append, atomic, digest, lock, protected_secret, read, require, resolve, sha, run_logged


def validate(m):
    require(m.get("schema") == "argus.experiment.v1", "unsupported experiment schema")
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,14}", m.get("experiment_id", "")), "invalid experiment_id (lowercase, at most 15 characters)")
    groups = m.get("groups", [])
    require(groups and len(groups) == len(set(groups)) and all(g in GROUPS for g in groups), "invalid groups")
    require(m.get("seeds") and all(type(s) is int for s in m["seeds"]) and len(set(m["seeds"])) == len(m["seeds"]), "invalid seeds")
    require(m.get("scales") and all(type(s) is int and s > 0 for s in m["scales"])
            and len(set(m["scales"])) == len(m["scales"]), "invalid scales")
    require(m.get("policy", {}).get("can_reattest") is False, "ordinary Agent renewal must retain CanReattest=false")
    require(m.get("scope") == "private_user_memories", "this suite tests private user memories")
    if any(case.get("experiment") == "E5" for case in m.get("cases", [])):
        require(m.get("load", {}).get("frozen") is True, "pilot load must be frozen for E5 measurements")
    require(m.get("secrets", {}) == {} or all(SAFE.fullmatch(k) and isinstance(v, str) for k, v in m["secrets"].items()), "invalid secret file references")
    names = set()
    for case in m.get("cases", []):
        require(SAFE.fullmatch(case.get("name", "")) and case["name"] not in names, "duplicate/invalid case")
        names.add(case["name"])
        require(case.get("experiment") in ["E1", "E2", "E3", "E4", "E5"], "invalid experiment")
        for field in ("seeds", "scales", "groups"):
            if field not in case:
                continue
            values = case[field]
            require(isinstance(values, list) and values and len(values) == len(set(values)), "case " + field + " must be nonempty and distinct")
            if field == "groups":
                require(all(v in groups for v in values), "case groups must belong to configured arms")
            else:
                require(all(type(v) is int and (field != "scales" or v > 0) for v in values), "invalid case " + field)
        ids = set()
        for op in case.get("operations", []):
            require(SAFE.fullmatch(op.get("id", "")) and op["id"] not in ids, "duplicate/invalid operation")
            ids.add(op["id"])
            require(op.get("role") in ("client", "server"), "operation needs client/server role")
            require(op.get("kind") in ("query", "mutation", "fault", "measure"), "invalid operation kind")
            require(isinstance(op.get("argv"), list) and op["argv"] and all(isinstance(a, str) for a in op["argv"]), "argv must be a nonempty string array")
            require(0 < op.get("timeout_s", 0) <= 86400, "bounded operation timeout required")
            require(op.get("result") and not Path(op["result"]).is_absolute() and ".." not in Path(op["result"]).parts, "result must be inside run directory")
            require(op.get("verdict_field"), "explicit evidence verdict field required")
            require(not any(a in ("--api-key", "--token", "--password", "--client-secret") for a in op["argv"]), "use protected secret file/environment references")
            if "resume_argv" in op:
                require(op.get("operation_id_file") and isinstance(op["resume_argv"], list)
                        and op["resume_argv"] and all(isinstance(a, str) for a in op["resume_argv"]), "resume needs persisted operation ID evidence")
            for key in ("operation_id_file", "baseline_trace", "milestone_file"):
                if key in op:
                    require(isinstance(op[key], str) and op[key] and not Path(op[key]).is_absolute()
                            and ".." not in Path(op[key]).parts, key + " must be inside run directory")
            if op["kind"] == "fault":
                require(op.get("baseline_trace") and op.get("milestone_file"), "fault needs independent traffic baseline and business milestone")
    require(names, "at least one case is required")
    # Distinct namespaces are mandatory even if arms are run sequentially.
    arms = m.get("arms", {})
    require(set(arms) == set(groups), "one isolated arm per group is required")
    for field in ("identity_namespace", "state_dir", "registration_namespace"):
        values = [arms[g].get(field) for g in groups]
        require(all(isinstance(v, str) and v for v in values) and len(set(values)) == len(values), "arms share " + field)
    return m


def plan(m):
    result = []
    for case in m["cases"]:
        for scale in case.get("scales", m["scales"]):
            for seed in case.get("seeds", m["seeds"]):
                groups = list(case.get("groups", m["groups"]))
                random.Random(digest([m["experiment_id"], case["name"], scale, seed])).shuffle(groups)
                block = "%s-n%d-s%d" % (case["name"], scale, seed)
                for group in groups:
                    result.append({"run_id": m["experiment_id"] + "-" + digest([case["name"], scale, seed, group])[:32],
                                   "block_id": block, "case": case["name"], "experiment": case["experiment"],
                                   "scale": scale, "seed": seed, "group": group})
    return result


def source_snapshot(base):
    def git(*args):
        p = subprocess.run(["git", "-C", str(base), *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return p.stdout if p.returncode == 0 else b""
    import hashlib
    return {"commit": git("rev-parse", "HEAD").decode().strip(),
            "tracked_patch_sha256": hashlib.sha256(git("diff", "HEAD", "--binary")).hexdigest(),
            "tool_sha256": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")}}


def prepare(config, output):
    config, output = Path(config).resolve(), Path(output).resolve()
    m = validate(read(config))
    with lock(output):
        require(not (output / "manifest.json").exists(), "experiment already prepared; use resume")
        inputs = {str(resolve(config.parent, p)): sha(resolve(config.parent, p)) for p in m.get("artifacts", [])}
        manifest = {"schema": "argus.prepared.v1", "config": m, "config_digest": digest(m),
                    "source_config": str(config), "base": str(config.parent), "artifacts": inputs,
                    "source": source_snapshot(config.parent), "runs": plan(m),
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "remote_acceptance": "NOT_RUN"}
        atomic(output / "manifest.json", manifest)
        atomic(output / "state.json", {"schema": "argus.runner-state.v1", "config_digest": digest(m), "operations": {}})
    return manifest


def selected_runs(saved, role, only_run=None, case=None, group=None):
    cases = {c["name"]: c for c in saved["config"]["cases"]}
    runs = [r for r in saved["runs"] if (not only_run or r["run_id"] == only_run)
            and (not case or r["case"] == case) and (not group or r["group"] == group)
            and any(op["role"] == role for op in cases[r["case"]]["operations"])]
    require(runs, "no runs match the selected role/run/case/group")
    return runs


def preflight(output, role, only_run=None, case=None, group=None):
    output = Path(output).resolve()
    saved = read(output / "manifest.json")
    m = validate(saved["config"])
    runs = selected_runs(saved, role, only_run, case, group)
    groups = {r["group"] for r in runs}
    case_names = {r["case"] for r in runs}
    operations = [op for item in m["cases"] if item["name"] in case_names for op in item["operations"] if op["role"] == role]
    used_fields = {field for op in operations for arg in op["argv"] + op.get("resume_argv", [])
                   for field in re.findall(r"\{([a-z_]+)\}", arg)}
    checks = []
    def check(name, ok):
        checks.append({"name": name, "result": "PASS" if ok else "FAIL"})
    check("config_unchanged", digest(read(saved["source_config"])) == saved["config_digest"])
    owners, unused = {}, set()
    for name, arm in m["arms"].items():
        references = list(arm.get("artifacts", []))
        references += [v for k, v in arm.items() if k.endswith("_config") and isinstance(v, str)]
        if arm.get("variant_output"):
            references.append(str(Path(arm["variant_output"]) / "variant.json"))
        for value in references:
            owners.setdefault(str(resolve(saved["base"], value)), set()).add(name)
        if name in groups:
            for field in ("business_config", "load_config"):
                if arm.get(field) and field not in used_fields:
                    unused.add(str(resolve(saved["base"], arm[field])))
    for path, expected in saved["artifacts"].items():
        if path in unused or (path in owners and not owners[path] & groups):
            continue
        check("artifact:" + path, Path(path).is_file() and sha(path) == expected)
    for name, path in m.get("secrets", {}).items():
        try:
            protected_secret(resolve(saved["base"], path))
            check("protected_secret:" + name, True)
        except (OSError, ValueError):
            check("protected_secret:" + name, False)
    for group, arm in m["arms"].items():
        if group not in groups:
            continue
        if arm.get("variant_output"):
            from variants import inspect_variant
            try:
                report = inspect_variant(resolve(saved["base"], arm["variant_output"]))
                check("variant:" + group, report.get("variant") == group and report.get("artifact_integrity") == "PASS"
                      and report.get("effective_config") == "PASS" and report.get("payload_ready") is True)
            except (ValueError, OSError):
                check("variant:" + group, False)
        else:
            check("variant:" + group, False)
    check("role_has_operations", bool(operations))
    result = {"schema": "argus.preflight.v1", "role": role, "run_ids": [r["run_id"] for r in runs], "checks": checks,
              "result": "PASS" if all(c["result"] == "PASS" for c in checks) else "FAIL",
              "remote_acceptance": "NOT_RUN"}
    atomic(output / ("preflight-" + role + ".json"), result)
    return result


def field(value, dotted):
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def fault_ready(trace, milestone, run_id):
    lanes = {"existing": [], "new": []}
    for line in Path(trace).read_text(encoding="utf-8").splitlines():
        row = __import__("json").loads(line)
        if row.get("type") == "request" and row.get("run_id") == run_id and row.get("lane") in lanes:
            lanes[row["lane"]].append(row)
    evidence = read(milestone)
    require(evidence.get("run_id") == run_id and evidence.get("reached") is True and evidence.get("milestone"), "business milestone not reached for this run")
    from milestone import verify as verify_milestone
    verify_milestone(evidence, run_id)
    for name, rows in lanes.items():
        baseline = rows[-3:]
        require(len(baseline) == 3 and all(row.get("ok") is True for row in baseline),
                "fault needs three recent successful baselines on EACH connection lane")
        require(len({row.get("request_id") for row in baseline}) == 3 and all(row.get("request_id") for row in baseline),
                "fault baseline requires distinct real requests")
        if name == "existing":
            require(all(row.get("tls_connections") == 1 for row in rows), "existing TLS lane reconnected or was never established")


def evidence_fingerprint(path):
    if not path.is_file():
        return None
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size, stat.st_ino, sha(path)


def evidence_intact(path, expected_sha256):
    """A step receipt also pins its native result, not just an optimistic label."""
    try:
        path = Path(path)
        if not path.is_file() or path.is_symlink() or sha(path) != expected_sha256:
            return False
        value = read(path)
        if value.get("schema") == "argus.step.v1":
            source = Path(value["source"])
            if not source.is_absolute():
                source = path.parent / source
            return source.is_file() and not source.is_symlink() and sha(source) == value.get("source_sha256")
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def safe_get_measurement(op, arm, base):
    """Only our body-free GET sampler may start an explicit replacement attempt."""
    argv = op["argv"]
    if not (op["kind"] == "measure" and op.get("safe_retry") == "get-measurement"
            and len(argv) > 3 and Path(argv[1]).resolve() == Path(__file__).with_name("step.py").resolve()
            and argv[2:4] == ["load", "run"] and arm.get("load_config")):
        return False
    c = read(resolve(base, arm["load_config"]))
    return not c.get("body_file") and all(not item.get("body_file") for item in c["instances"])


def execute(output, role, resume=False, only_run=None, case=None, group=None, new_attempt=False):
    output = Path(output).resolve()
    with lock(output):
        require(not new_attempt or (resume and only_run), "new attempt requires resume with one explicit --run-id")
        require(preflight(output, role, only_run, case, group)["result"] == "PASS", "preflight failed")
        saved, state = read(output / "manifest.json"), read(output / "state.json")
        require(state["config_digest"] == saved["config_digest"], "state/config mismatch")
        m = saved["config"]
        env = os.environ.copy()
        for name, path in m.get("secrets", {}).items():
            env[name] = protected_secret(resolve(saved["base"], path))
        cases = {c["name"]: c for c in m["cases"]}
        for run in selected_runs(saved, role, only_run, case, group):
            for op in cases[run["case"]].get("operations", []):
                if op["role"] != role:
                    continue
                key = run["run_id"] + "/" + op["id"]
                old = state["operations"].get(key)
                if new_attempt:
                    require(old and old["phase"] in ("interrupted", "submission_unknown"), "new attempt requires an interrupted measurement")
                    require(safe_get_measurement(op, m["arms"][run["group"]], saved["base"]),
                            "new attempts are only supported for body-free GET measurements; mutations/faults/POST cannot replay")
                if old and old["phase"] == "complete":
                    path = output / old["evidence"]
                    require(evidence_intact(path, old["evidence_sha256"]),
                            "completed evidence changed; cannot silently skip: " + key)
                    continue
                require(not old or resume, "existing operation requires resume")
                attempt = old.get("attempt", 1) + 1 if new_attempt else old.get("attempt", 1) if old else 1
                run_root = output / "runs" / run["run_id"]
                directory = run_root if attempt == 1 else run_root / "attempts" / str(attempt)
                directory.mkdir(parents=True, exist_ok=True)
                atomic(directory / "run.json", dict(run, attempt=attempt))
                context = dict(run, run_dir=str(directory), base=saved["base"], operation_id=digest(key if attempt == 1 else [key, attempt])[:24],
                               state_dir=m["arms"][run["group"]]["state_dir"])
                context.update({k: str(v) for k, v in m["arms"][run["group"]].items() if isinstance(v, (str, int))})
                argv = op["argv"]
                if old and not new_attempt and op["kind"] != "query":
                    require(op["kind"] != "measure", "interrupted measurement needs an explicit --new-attempt; partial windows cannot resume")
                    receipt = directory / op.get("operation_id_file", "missing-operation-id")
                    require(op.get("resume_argv") and receipt.is_file(), "unknown mutation/creation cannot be replayed: " + key)
                    known = read(receipt)
                    require(known.get("operation_id") and known.get("run_id") == run["run_id"], "recovery receipt has no bound operation ID")
                    context["known_operation_id"] = str(known["operation_id"])
                    argv = op["resume_argv"]
                if op["kind"] == "fault" and not old:
                    fault_ready(directory / op["baseline_trace"], directory / op["milestone_file"], run["run_id"])
                argv = [a.format_map(context) for a in argv]
                result_path = directory / op["result"]
                # A stale result must not turn an interrupted invocation into success.
                require(not result_path.exists() or (old and op.get("resume_argv")), "result path already exists for incomplete operation; reconcile it explicitly")
                if result_path.exists():
                    atomic(directory / ".receipts" / (op["id"] + "-" + str(time.time_ns()) + ".json"), read(result_path))
                previous_result = evidence_fingerprint(result_path)
                previous = list(old.get("previous_attempts", [])) if old else []
                if new_attempt:
                    previous.append({k: v for k, v in old.items() if k != "previous_attempts"})
                entry = dict(run, id=op["id"], operation_id=context["operation_id"], phase="interrupted" if op["kind"] == "measure" else "submission_unknown",
                             attempt=attempt, previous_attempts=previous, evidence_dir=str(directory.relative_to(output)),
                             kind=op["kind"], role=role, argv_sha256=digest(argv), started_at_ms=time.time_ns() // 1000000)
                state["operations"][key] = entry
                atomic(output / "state.json", state)
                env.update(ARGUS_RUN_ID=run["run_id"], ARGUS_OPERATION_ID=context["operation_id"],
                           ARGUS_SEED=str(run["seed"]), ARGUS_BLOCK_ID=run["block_id"],
                           ARGUS_GROUP=run["group"], ARGUS_SCALE=str(run["scale"]), ARGUS_ATTEMPT=str(attempt))
                try:
                    p = run_logged(argv, cwd=saved["base"], env=env, timeout=op["timeout_s"],
                                   diagnostic=directory / (op["id"] + "-diagnostic.json"), stage=op["id"],
                                   secrets=[env[name] for name in m.get("secrets", {})])
                    entry["exit_code"] = p.returncode
                except (subprocess.TimeoutExpired, OSError) as exc:
                    entry["error"] = type(exc).__name__
                    atomic(output / "state.json", state)
                    append(output / "events.jsonl", entry)
                    raise RuntimeError("operation result unknown; inspect evidence and resume: " + key) from None
                entry["completed_at_ms"] = time.time_ns() // 1000000
                if not result_path.is_file() or evidence_fingerprint(result_path) == previous_result:
                    atomic(output / "state.json", state)
                    append(output / "events.jsonl", entry)
                    raise RuntimeError("operation failed or missing fresh evidence: " + key)
                evidence = read(result_path)
                require(evidence.get("run_id") == run["run_id"] and evidence.get("operation_id") == context["operation_id"],
                        "result does not bind current run and operation: " + key)
                value = field(evidence, op["verdict_field"])
                verdict = value if value in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN") else "UNKNOWN"
                if p.returncode != 0 and verdict == "PASS":
                    verdict = "UNKNOWN"
                entry.update(phase="complete", verdict=verdict, evidence=str(result_path.relative_to(output)), evidence_sha256=sha(result_path))
                if verdict == "UNKNOWN":
                    entry["phase"] = ("complete" if evidence.get("measurement_complete") is True else "interrupted") if op["kind"] == "measure" else "submission_unknown"
                atomic(output / "state.json", state)
                append(output / "events.jsonl", entry)
                if verdict != "PASS":
                    return {"result": verdict, "operation": key}
        return {"result": "COMPLETE", "remote_acceptance": "inspect per-run evidence"}


def reconcile(output, operation, expected_sha256):
    """Operator adoption of a completed evidence artifact after supervisor loss.

    Explicit artifact checksum prevents adopting whichever file happens to be
    present. It does not retry the operation, launch or write.
    """
    output = Path(output).resolve()
    with lock(output):
        saved, state = read(output / "manifest.json"), read(output / "state.json")
        old = state["operations"][operation]
        require(old["phase"] in ("submission_unknown", "interrupted"), "only unknown operations need reconciliation")
        case = next(c for c in saved["config"]["cases"] if c["name"] == old["case"])
        op = next(o for o in case["operations"] if o["id"] == old["id"])
        path = output / old.get("evidence_dir", "runs/" + old["run_id"]) / op["result"]
        require(evidence_intact(path, expected_sha256), "reconciled evidence hash mismatch")
        evidence = read(path)
        require(evidence.get("run_id") == old["run_id"] and evidence.get("operation_id") == old["operation_id"], "adopted evidence must bind run and operation")
        value = field(evidence, op["verdict_field"])
        require(value in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN"), "missing verdict")
        old.update(phase="complete", verdict=value, evidence=str(path.relative_to(output)), evidence_sha256=expected_sha256,
                   reconciled=True, reconciled_at_ms=time.time_ns() // 1000000)
        atomic(output / "state.json", state)
        append(output / "events.jsonl", old)
        return {"result": value, "replayed": False}


def collect(output):
    output = Path(output).resolve()
    with lock(output):
        saved, state = read(output / "manifest.json"), read(output / "state.json")
        cases = {c["name"]: c for c in saved["config"]["cases"]}
        rows = []
        for run in saved["runs"]:
            ops = []
            for op in cases[run["case"]].get("operations", []):
                entry = state["operations"].get(run["run_id"] + "/" + op["id"])
                if not entry:
                    ops.append("NOT_RUN")
                elif entry["phase"] != "complete":
                    ops.append("UNKNOWN")
                else:
                    path = output / entry["evidence"]
                    ops.append(entry["verdict"] if evidence_intact(path, entry["evidence_sha256"]) else "UNKNOWN")
            verdict = "FAIL" if "FAIL" in ops else "UNKNOWN" if "UNKNOWN" in ops else "NOT_RUN" if not ops or "NOT_RUN" in ops else "PASS"
            row = dict(run, result=verdict)
            measurements = [state["operations"].get(run["run_id"] + "/" + op["id"])
                            for op in cases[run["case"]].get("operations", []) if op["kind"] == "measure"]
            measurements = [entry for entry in measurements if entry]
            if measurements:
                entry = measurements[-1]
                row.update(attempt=entry.get("attempt", 1), evidence_dir=entry.get("evidence_dir", "runs/" + run["run_id"]),
                           interrupted_attempts=len(entry.get("previous_attempts", [])))
            rows.append(row)
        result = {"schema": "argus.collection.v1", "config_digest": saved["config_digest"], "runs": rows}
        atomic(output / "verdicts.json", result)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("prepare"); a.add_argument("--config", required=True); a.add_argument("--output", required=True)
    for name in ("preflight", "run", "resume"):
        a = sub.add_parser(name); a.add_argument("--output", required=True); a.add_argument("--role", choices=["client", "server"], required=True)
        a.add_argument("--run-id"); a.add_argument("--case"); a.add_argument("--group", choices=GROUPS)
        if name == "resume": a.add_argument("--new-attempt", action="store_true", help="start a fresh GET measurement window; retain the interrupted attempt")
    for name in ("collect", "analyze"):
        a = sub.add_parser(name); a.add_argument("--output", required=True)
    a = sub.add_parser("reconcile"); a.add_argument("--output", required=True); a.add_argument("--operation", required=True); a.add_argument("--sha256", required=True)
    args = p.parse_args()
    if args.command == "prepare": result = prepare(args.config, args.output)
    elif args.command == "preflight": result = preflight(args.output, args.role, args.run_id, args.case, args.group)
    elif args.command in ("run", "resume"): result = execute(args.output, args.role, args.command == "resume", args.run_id, args.case, args.group, getattr(args, "new_attempt", False))
    elif args.command == "collect": result = collect(args.output)
    elif args.command == "reconcile": result = reconcile(args.output, args.operation, args.sha256)
    else:
        from analysis import analyze
        result = analyze(args.output)
    print(__import__("json").dumps(result, indent=2))
    return 1 if result.get("result") in ("FAIL", "UNKNOWN") else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
