"""Finite SSH observer integration; local fixtures do not measure remote closure."""
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fault_trial
from common import atomic, digest
from test_timeline import evidence


def config(tmp_path, enabled=True):
    value = dict(run_id="run", ssh_host="server", server_python="python3", server_deployment="/etc/argus/config.json",
                 server_remote_acceptance="/repo/remote_acceptance.py", server_receiver_package="/repo/adapters",
                 fault_file="/evidence/fault.jsonl", receiver_file="/evidence/receiver.jsonl", collector_control="/run/collector.sock",
                 event="helper-freeze", bound_ms=1000, clock_uncertainty_ms=0, milestone_file=str(tmp_path / "milestone.json"),
                 baseline_budget_s=5, diagnostic_dir=str(tmp_path),
                 probe=dict(method="POST", body_file="/fixtures/body.json", inflight=False, duration=2))
    if enabled:
        value["timeline"] = dict(server_tool="/repo/timeline.py", output="/evidence/lifecycle.jsonl", stop_file="/evidence/lifecycle.stop")
    return value


class Child:
    def __init__(self):
        self.stderr = io.BytesIO()
        self.returncode = None
        self.pid = 12
    def poll(self): return self.returncode
    def wait(self, timeout=None): self.returncode = 0; return 0
    def terminate(self): self.returncode = -15
    def kill(self): self.returncode = -9


def test_observer_is_foreground_bounded_ssh_and_stop_only_signals_its_file(tmp_path):
    c, child = config(tmp_path), Child()
    with patch.object(fault_trial.subprocess, "Popen", return_value=child) as popen:
        handle = fault_trial.start_lifecycle(c, tmp_path)
    command = popen.call_args.args[0]
    assert command[:3] == ["ssh", "-o", "BatchMode=yes"]
    assert " timeline.py " not in command[-1]  # Path is explicit.
    assert "observe --config" in command[-1] and "--duration 52" in command[-1]
    assert "nohup" not in command[-1] and "systemd-run" not in command[-1]
    with patch.object(fault_trial, "remote") as remote:
        assert fault_trial.finish_lifecycle(c, tmp_path, handle)["status"] == "COMPLETED"
        assert fault_trial.finish_lifecycle(c, tmp_path, handle)["status"] == "COMPLETED"
    remote.assert_called_once()
    assert remote.call_args.args[1][-1] == "/evidence/lifecycle.stop"
    assert "systemctl" not in remote.call_args.args[1]


def test_observer_requires_real_ready_row_with_matching_run(tmp_path):
    c = config(tmp_path)
    handle = {"child": Child(), "ready": False}
    def reply(*args, **kwargs):
        kwargs["output"].write_text('{"type":"observer_ready","run_id":"other"}\n')
    with patch.object(fault_trial, "remote", side_effect=reply):
        assert not fault_trial.lifecycle_ready(c, tmp_path, handle)
    def good(*args, **kwargs):
        kwargs["output"].write_text('{"type":"observer_ready","run_id":"run"}\n')
    with patch.object(fault_trial, "remote", side_effect=good):
        assert fault_trial.lifecycle_ready(c, tmp_path, handle)


def test_legacy_configuration_and_distinct_evidence_paths(tmp_path):
    c = config(tmp_path, False)
    assert fault_trial.start_lifecycle(c, tmp_path) is None
    assert fault_trial.finish_lifecycle(c, tmp_path, None) == {"status": "NOT_RUN"}
    c = config(tmp_path)
    c["timeline"]["stop_file"] = c["fault_file"]
    with pytest.raises(ValueError): fault_trial.lifecycle_config(c)


def test_missing_collection_input_replaces_current_pass_but_preserves_prior_result(tmp_path):
    atomic(tmp_path / "result.json", {"run_id": "run", "result": "PASS"})
    with pytest.raises(FileNotFoundError): fault_trial.collect(config(tmp_path), tmp_path)
    current = json.loads((tmp_path / "result.json").read_text())
    assert current["result"] == "UNKNOWN"
    assert current["phase"] == "collection_incomplete"
    previous = json.loads((tmp_path / current["source_directory"] / "previous-result.json").read_text())
    assert previous["result"] == "PASS"


@pytest.mark.parametrize("gap", [None, "observer_gap", "failed_samples"])
def test_collect_preserves_raw_sources_and_recomputable_timeline(tmp_path, gap):
    c = config(tmp_path)
    fault, receiver, probe = evidence()
    (tmp_path / "trace.jsonl").write_text("".join(json.dumps(r) + "\n" for r in probe))
    lifecycle = [dict(type="observer_start", run_id="run", at_ms=0),
                 dict(type="observer_ready", run_id="run", at_ms=1),
                 dict(type="detected", run_id="run", at_ms=5250, observed_after_at_ms=5100,
                      verified=True, source="poll", timing="poll_interval", boundary="readiness_withdrawal_or_expiry"),
                 dict(type="observer_stop", run_id="run", at_ms=10000, complete=True, healthy_baseline=True)]
    if gap == "observer_gap":
        lifecycle.insert(-1, dict(type="observer_gap", run_id="run", at_ms=5300, error="TimeoutExpired"))
    elif gap == "failed_samples":
        lifecycle[-1]["failed_samples"] = 1
    def remote(_c, argv, **kwargs):
        rows = [fault] if argv[-1] == c["fault_file"] else receiver if argv[-1] == c["receiver_file"] else lifecycle
        kwargs["output"].write_text("".join(json.dumps(r) + "\n" for r in rows))
    def check(argv, **kwargs):
        atomic(argv[argv.index("--output") + 1], dict(result="PASS", run_id="run"))
        return subprocess.CompletedProcess(argv, 0)
    with patch.object(fault_trial, "remote", side_effect=remote), patch.object(fault_trial, "run_logged", side_effect=check):
        result = fault_trial.collect(c, tmp_path)
    assert result["lifecycle_observation"] == ("COMPLETE" if gap is None else "UNKNOWN")
    report = json.loads((tmp_path / result["timeline_path"]).read_text())
    assert report["detection"]["lower_ms"] == 100
    assert report["detection"]["upper_ms"] == 250
    assert report["entry_stop"]["status"] == "UNKNOWN"
    assert set(result["sources_sha256"]) == {"trace.jsonl", "fault.jsonl", "receiver.jsonl", "lifecycle.jsonl"}
    assert fault_trial.sha(tmp_path / result["timeline_path"]) == result["timeline_sha256"]
    assert report["sources_sha256"] == result["sources_sha256"]
    assert all(fault_trial.sha(tmp_path / result["source_directory"] / name) == checksum
               for name, checksum in result["sources_sha256"].items())


@pytest.mark.parametrize("fault_fails", [False, True])
def test_run_starts_observer_before_fault_and_unknown_submission_is_not_replayed(tmp_path, fault_fails):
    c = config(tmp_path)
    path = tmp_path / "config.json"
    atomic(path, c)
    output = tmp_path / "trial"
    children, commands = [Child(), Child()], []
    def remote(_c, argv, **kwargs):
        commands.append(argv)
        if argv[:2] == ["cat", "--"]:
            kwargs["output"].write_text('{"type":"observer_ready","run_id":"run"}\n')
        if "--execute-fault" in argv:
            state = json.loads((output / "state.json").read_text())
            assert state["phase"] == "fault_submission_unknown"
            assert any(cmd[:2] == ["cat", "--"] for cmd in commands[:-1])
            if fault_fails:
                raise subprocess.TimeoutExpired(argv, 40)
    with patch.object(fault_trial.subprocess, "Popen", side_effect=children) as popen, \
            patch.object(fault_trial, "resolve_credentials", return_value={}), \
            patch.object(fault_trial, "fault_ready"), patch.object(fault_trial, "remote", side_effect=remote), \
            patch.object(fault_trial, "collect", return_value={"result": "PASS"}) as collect, \
            patch.object(fault_trial.time, "sleep"):
        if fault_fails:
            with pytest.raises(subprocess.TimeoutExpired): fault_trial.run(path, output)
            assert json.loads((output / "state.json").read_text())["phase"] == "fault_submission_unknown"
            collect.assert_not_called()
        else:
            assert fault_trial.run(path, output)["result"] == "PASS"
        assert popen.call_count == 2
        assert sum("--execute-fault" in argv for argv in commands) == 1
        assert all(child.poll() is not None for child in children)
    with patch.object(fault_trial.subprocess, "Popen") as popen, patch.object(fault_trial, "collect", return_value={"result": "UNKNOWN"}):
        assert fault_trial.run(path, output, resume=True)["result"] == "UNKNOWN"
        popen.assert_not_called()
