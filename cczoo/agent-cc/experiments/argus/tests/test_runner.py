import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runner
from common import atomic, lock, read, sha
from analysis import analyze, mean_ci, paired_difference, summarize_requests
from locomo import convert


def config():
    return {"schema": "argus.experiment.v1", "experiment_id": "test-suite", "groups": ["full_argus", "no_close"],
            "seeds": [1, 2], "scales": [1], "policy": {"can_reattest": False}, "scope": "private_user_memories",
            "load": {"frozen": True, "rate": 1}, "secrets": {},
            "arms": {g: {"identity_namespace": g, "state_dir": "/tmp/" + g, "registration_namespace": g} for g in ("full_argus", "no_close")},
            "cases": [{"name": "memory", "experiment": "E4", "operations": [{"id": "business", "role": "client", "kind": "mutation",
                       "argv": [sys.executable, "worker.py", "{run_dir}", "{operation_id}"], "timeout_s": 5, "result": "result.json", "verdict_field": "result"}]}]}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg, self.out = self.root / "config.json", self.root / "experiment"
        atomic(self.cfg, config())
        (self.root / "worker.py").write_text('import json, pathlib, sys, os\np=pathlib.Path(sys.argv[1]); (p/"result.json").write_text(json.dumps({"result":"PASS","run_id":os.environ["ARGUS_RUN_ID"],"operation_id":sys.argv[2]}))\n', encoding="utf-8")

    def test_paired_randomization_and_configuration_rejection(self):
        m = config()
        plan = runner.plan(m)
        self.assertEqual(plan, runner.plan(m))
        self.assertEqual(len(plan), 4)
        for seed in m["seeds"]:
            self.assertEqual({r["group"] for r in plan if r["seed"] == seed}, set(m["groups"]))
        for mutate in (lambda c: c["arms"]["no_close"].update(state_dir="/tmp/full_argus"),
                       lambda c: c["policy"].update(can_reattest=True)):
            bad = copy.deepcopy(m); mutate(bad)
            with self.assertRaises(ValueError): runner.validate(bad)

    def test_unrun_never_becomes_pass_and_reports_regenerate(self):
        runner.prepare(self.cfg, self.out)
        result = runner.collect(self.out)
        self.assertEqual({r["result"] for r in result["runs"]}, {"NOT_RUN"})
        analyze(self.out)
        first = (self.out / "statistics.csv").read_bytes()
        analyze(self.out)
        self.assertEqual(first, (self.out / "statistics.csv").read_bytes())
        self.assertTrue((self.out / "verdicts.svg").is_file())

    def test_real_child_execution_complete_resume_does_not_replay(self):
        runner.prepare(self.cfg, self.out)
        with patch.object(runner, "preflight", return_value={"result": "PASS"}):
            runner.execute(self.out, "client")
            before = (self.out / "events.jsonl").read_bytes()
            runner.execute(self.out, "client", resume=True)
            self.assertEqual(before, (self.out / "events.jsonl").read_bytes())
        self.assertEqual({r["result"] for r in runner.collect(self.out)["runs"]}, {"PASS"})
        state = read(self.out / "state.json")
        p = self.out / next(iter(state["operations"].values()))["evidence"]
        p.write_text('{}')
        self.assertIn("UNKNOWN", {r["result"] for r in runner.collect(self.out)["runs"]})

    def test_unknown_mutation_never_replayed(self):
        (self.root / "worker.py").write_text('import sys\nsys.exit(1)\n')
        runner.prepare(self.cfg, self.out)
        with patch.object(runner, "preflight", return_value={"result": "PASS"}):
            with self.assertRaises(RuntimeError): runner.execute(self.out, "client")
            with patch.object(runner.subprocess, "run") as child:
                with self.assertRaisesRegex(ValueError, "cannot be replayed"): runner.execute(self.out, "client", resume=True)
                child.assert_not_called()

    def test_known_operation_recovery_uses_query(self):
        m = config(); op = m["cases"][0]["operations"][0]
        op.update(resume_argv=[sys.executable, "worker.py", "{run_dir}", "{operation_id}"], operation_id_file="known.json")
        atomic(self.cfg, m); runner.prepare(self.cfg, self.out)
        state = read(self.out / "state.json"); run = runner.plan(m)[0]
        key = run["run_id"] + "/business"
        state["operations"][key] = dict(run, phase="submission_unknown")
        atomic(self.out / "state.json", state)
        atomic(self.out / "runs" / run["run_id"] / "known.json", {"run_id": run["run_id"], "operation_id": "task-123"})
        with patch.object(runner, "preflight", return_value={"result": "PASS"}):
            runner.execute(self.out, "client", resume=True)
        self.assertEqual(read(self.out / "state.json")["operations"][key]["phase"], "complete")

    def test_negative_verdict_persisted_even_nonzero_exit(self):
        (self.root / "worker.py").write_text('import pathlib,sys,os,json\n(pathlib.Path(sys.argv[1])/"result.json").write_text(json.dumps({"result":"FAIL","run_id":os.environ["ARGUS_RUN_ID"],"operation_id":sys.argv[2]}))\nsys.exit(1)\n')
        runner.prepare(self.cfg, self.out)
        with patch.object(runner, "preflight", return_value={"result": "PASS"}):
            self.assertEqual(runner.execute(self.out, "client")["result"], "FAIL")
        self.assertIn("FAIL", {r["result"] for r in runner.collect(self.out)["runs"]})

    def test_lock_and_changed_config_preflight(self):
        runner.prepare(self.cfg, self.out)
        with lock(self.out):
            with self.assertRaises(OSError):
                with lock(self.out): pass
        m = config(); m["seeds"] = [4]; atomic(self.cfg, m)
        report = runner.preflight(self.out, "client")
        self.assertEqual(next(c for c in report["checks"] if c["name"] == "config_unchanged")["result"], "FAIL")

    @patch('milestone.verify', return_value=True)  # Real source binding has dedicated milestone tests.
    def test_fault_requires_both_lanes_and_business_milestone(self, _milestone):
        trace, milestone = self.root / "trace.jsonl", self.root / "milestone.json"
        rows = [{"type": "request", "run_id": "r", "request_id": str(i), "tls_connections": 1, "ok": True, "lane": lane} for i, lane in enumerate(["existing"] * 5 + ["new"] * 2)]
        trace.write_text("\n".join(map(json.dumps, rows))); atomic(milestone, {"run_id": "r", "milestone": "nonempty-extraction", "reached": True})
        with self.assertRaises(ValueError): runner.fault_ready(trace, milestone, "r")
        rows.append({"type": "request", "run_id": "r", "request_id": "last", "tls_connections": 1, "ok": True, "lane": "new"}); trace.write_text("\n".join(map(json.dumps, rows)))
        runner.fault_ready(trace, milestone, "r")
        with self.assertRaises(ValueError): runner.fault_ready(trace, milestone, "different")

    def test_paired_stats_and_warmup_exclusion(self):
        rows = [{"block_id": str(i), "case": "c", "scale": 1, "group": g, "v": v} for i in range(3) for g, v in (("full_argus", 10), ("no_close", 8))]
        self.assertEqual(paired_difference(rows, "no_close", "v")["mean"], 2)
        self.assertIsNone(mean_ci([1])["low"])
        r = summarize_requests([{"phase": "warmup", "outcome": "success", "latency_ms": 900}, {"phase": "measurement", "outcome": "success", "latency_ms": 10}, {"phase": "measurement", "outcome": "timeout"}], 2)
        self.assertEqual((r["p95_ms"], r["api_goodput_rps"], r["timeout"]), (10, .5, 1))

    def test_locomo_derived_not_official_and_no_answer_leak(self):
        source = self.root / "locomo.json"
        atomic(source, [{"sample_id": "x", "conversation": {"session_1": [{"speaker": "a", "text": "fact", "dia_id": "D1:1"}]},
                         "qa": [{"category": 1, "question": "What?", "answer": "answer"}, {"category": 1, "question": "Is answer here?", "answer": "answer"}]}])
        result = convert(source, self.root / "derived.json")
        self.assertEqual(len(result["tasks"]), 1); self.assertEqual(result["model_score"], "NOT_RUN")


if __name__ == "__main__": unittest.main()
