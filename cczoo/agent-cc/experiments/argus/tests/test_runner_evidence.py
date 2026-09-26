"""Never turn stale/wrong-run artifacts into completed paper evidence."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fault_trial
import runner
from common import atomic, digest, read, sha
from test_runner import config


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg, self.out = self.root / "config.json", self.root / "out"

    def test_wrong_run_fresh_pass_is_not_adopted(self):
        m = config()
        m["cases"][0]["operations"][0]["argv"] = [sys.executable, "worker.py", "{run_dir}", "{operation_id}"]
        (self.root / "worker.py").write_text('import json,pathlib,sys\np=pathlib.Path(sys.argv[1]); (p/"result.json").write_text(json.dumps({"result":"PASS","run_id":"wrong","operation_id":sys.argv[2]}))')
        atomic(self.cfg, m); runner.prepare(self.cfg, self.out)
        with patch.object(runner, "preflight", return_value={"result": "PASS"}), self.assertRaisesRegex(ValueError, "bind current run"):
            runner.execute(self.out, "client")
        self.assertIn("UNKNOWN", {r["result"] for r in runner.collect(self.out)["runs"]})

    def test_step_receipt_cannot_outlive_lost_or_changed_native_evidence(self):
        native = self.root / "business.json"
        envelope = self.root / "receipt.json"
        atomic(native, {"result": "PASS", "run_id": "trial"})
        atomic(envelope, {"schema": "argus.step.v1", "result": "PASS", "source": native.name, "source_sha256": sha(native)})
        pin = sha(envelope)
        self.assertTrue(runner.evidence_intact(envelope, pin))
        copied = self.root / "copied-evidence"
        copied.mkdir()
        shutil.copy2(native, copied / native.name)
        shutil.copy2(envelope, copied / envelope.name)
        self.assertTrue(runner.evidence_intact(copied / envelope.name, pin))
        atomic(native, {"result": "UNKNOWN", "run_id": "trial"})
        self.assertFalse(runner.evidence_intact(envelope, pin))
        native.unlink()
        self.assertFalse(runner.evidence_intact(envelope, pin))

    def test_resume_noop_cannot_adopt_old_pass_and_native_result_preserved(self):
        m = config()
        op = m["cases"][0]["operations"][0]
        op.update(resume_argv=[sys.executable, "-c", "pass"], operation_id_file="known.json")
        atomic(self.cfg, m); runner.prepare(self.cfg, self.out)
        state = read(self.out / "state.json")
        run = runner.plan(m)[0]
        key = run["run_id"] + "/business"
        state["operations"][key] = dict(run, phase="submission_unknown")
        atomic(self.out / "state.json", state)
        directory = self.out / "runs" / run["run_id"]
        atomic(directory / "known.json", {"run_id": run["run_id"], "operation_id": "task-1"})
        atomic(directory / "result.json", {"result": "PASS", "run_id": run["run_id"], "operation_id": digest(key)[:24]})
        before = (directory / "result.json").read_bytes()
        with patch.object(runner, "preflight", return_value={"result": "PASS"}), self.assertRaisesRegex(RuntimeError, "fresh evidence"):
            runner.execute(self.out, "client", resume=True)
        self.assertEqual((directory / "result.json").read_bytes(), before)
        self.assertEqual(read(self.out / "state.json")["operations"][key]["phase"], "submission_unknown")

    def test_invalid_recovery_paths_and_duplicate_scales_rejected(self):
        for update in ({"operation_id_file": "../other.json", "resume_argv": ["query"]}, {"resume_argv": []}):
            m = config(); m["cases"][0]["operations"][0].update(update)
            with self.assertRaises(ValueError): runner.validate(m)
        m = config(); m["scales"] = [1, 1]
        with self.assertRaises(ValueError): runner.validate(m)

    def test_case_overrides_and_collector_safe_run_id(self):
        import re
        m = config()
        m["cases"][0].update(scales=[3], seeds=[0, 1, 2, 3, 4], groups=["full_argus"])
        runner.validate(m)
        runs = runner.plan(m)
        self.assertEqual(len(runs), 5)
        self.assertEqual({r["scale"] for r in runs}, {3})
        self.assertEqual({r["group"] for r in runs}, {"full_argus"})
        self.assertTrue(all(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", r["run_id"]) for r in runs))
        self.assertEqual(len({r["run_id"] for r in runs}), 5)
        m["cases"][0]["groups"] = ["static_mtls"]
        with self.assertRaises(ValueError): runner.validate(m)

    @patch("milestone.verify", return_value=True)
    def test_fault_does_not_run_on_reconnected_or_failed_recent_baseline(self, _verified_milestone):
        milestone, trace = self.root / "milestone.json", self.root / "trace.jsonl"
        atomic(milestone, {"run_id": "trial", "milestone": "archive", "reached": True})
        rows = [{"type": "request", "run_id": "trial", "request_id": str(i), "ok": True,
                 "lane": lane, "tls_connections": 1} for i, lane in enumerate(["existing"] * 3 + ["new"] * 3)]
        rows[0]["tls_connections"] = 2
        trace.write_text("\n".join(map(json.dumps, rows)))
        with self.assertRaisesRegex(ValueError, "reconnected"):
            runner.fault_ready(trace, milestone, "trial")
        rows[0]["tls_connections"] = 1
        rows.append({**rows[-1], "request_id": "failure", "ok": False})
        trace.write_text("\n".join(map(json.dumps, rows)))
        with self.assertRaisesRegex(ValueError, "recent successful"):
            runner.fault_ready(trace, milestone, "trial")

    def test_failed_assessor_cannot_read_stale_pass(self):
        atomic(self.root / "result.json", {"result": "PASS", "run_id": "trial"})
        (self.root / "trace.jsonl").write_text("")
        config = {"run_id": "trial", "fault_file": "/remote/fault", "receiver_file": "/remote/receiver", "bound_ms": 5000, "clock_uncertainty_ms": 1}
        with patch.object(fault_trial, "remote"), patch.object(fault_trial, "run_logged", return_value=subprocess.CompletedProcess([], 2)), self.assertRaisesRegex(ValueError, "fresh assessment"):
            fault_trial.collect(config, self.root)
        self.assertEqual(read(self.root / "result.json")["result"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
