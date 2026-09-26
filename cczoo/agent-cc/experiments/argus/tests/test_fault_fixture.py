"""No systemd or Docker mutations: only isolated fixture-file inode writes."""
import copy
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fault_fixture as fixture


@pytest.fixture
def environment(tmp_path, monkeypatch):
    live, original, replacement = [tmp_path / name for name in ("live.json", "original.json", "replacement.json")]
    first = b'{"synthetic":1}'
    second = b'{"synthetic":2}'
    live.write_bytes(first); original.write_bytes(first); replacement.write_bytes(second)
    config = {"approved": {"config_digest": "sha256:" + fixture.sha_bytes(first)}, "test": True}
    config_file = tmp_path / "environment.json"; config_file.write_text(json.dumps(config))
    target = {"pid": "123", "start_time": "456", "workload_id": "memory", "agent_id": "spiffe://test/node",
              "container_id": "c" * 64, "launch_id": "launch", "config_digest": config["approved"]["config_digest"],
              "image_config_digest": "sha256:" + "a" * 64}
    deployment = SimpleNamespace(workload={"id": "memory", "config_host_path": str(live)},
                                 identity={"agent_id": "spiffe://test/node"}, bin=tmp_path, target=tmp_path / "target.json")
    calls = []
    runtime = SimpleNamespace(Deployment=lambda c: deployment, run=lambda argv: calls.append(argv))
    def checkpoint(path):
        return json.loads(Path(path).read_text().splitlines()[-1])
    observer = SimpleNamespace(now_ms=lambda: 1000, hold_path=lambda d: tmp_path / "hold",
                               hold_contents=lambda owner: b"hold", fault_checkpoint=checkpoint,
                               hold_recovery=lambda *args: {"verified": True, "restart": "no"})
    monkeypatch.setattr(fixture, "load_runtime", lambda config_file: (runtime, observer, config))
    # Root/path verification has a separate test. These tests use a temporary
    # inode only and never touch a production /srv or /proc path.
    monkeypatch.setattr(fixture, "protected", lambda path, **kw: Path(path))
    monkeypatch.setattr(fixture, "actual_target", lambda *args: copy.deepcopy(target))
    monkeypatch.setattr(fixture, "same_instance", lambda target: None)
    monkeypatch.setattr(fixture, "view_path", lambda target: live)
    args = SimpleNamespace(config=str(config_file), event="config-change", output=str(tmp_path / "fault.jsonl"),
                           run_id="trial", execute_fault=True, hold_recovery=True, original=str(original),
                           replacement=str(replacement), original_sha256=fixture.sha_bytes(first),
                           replacement_sha256=fixture.sha_bytes(second))
    return SimpleNamespace(args=args, live=live, original=original, replacement=replacement, first=first,
                           second=second, target=target, calls=calls, observer=observer)


@pytest.mark.skipif(sys.platform != "linux", reason="real file-lock and bind-inode mutation on Linux")
def test_config_mutation_uses_same_inode_and_explicit_restore(environment):
    e = environment
    inode = e.live.stat().st_ino
    before = time.monotonic_ns()
    record = fixture.inject(e.args)
    assert record["executed"] is True
    assert before <= record["started_monotonic_ns"] <= time.monotonic_ns()
    assert e.live.stat().st_ino == inode
    assert e.live.read_bytes() == e.second
    assert "synthetic" not in Path(e.args.output).read_text()
    restored = fixture.restore(SimpleNamespace(config=e.args.config, fault=e.args.output, execute_restore=True))
    assert restored["restored"] is True and restored["admission"] == "NOT_RUN"
    assert e.live.read_bytes() == e.first and e.live.stat().st_ino == inode
    with pytest.raises(ValueError, match="already attempted"):
        fixture.restore(SimpleNamespace(config=e.args.config, fault=e.args.output, execute_restore=True))


@pytest.mark.skipif(sys.platform != "linux", reason="durable Linux mutation journal")
def test_unknown_injection_never_replays_and_records_no_config(environment, monkeypatch):
    e = environment
    writes = []
    def broken(*args):
        writes.append(True)
        raise TimeoutError("SECRET-must-not-log")
    monkeypatch.setattr(fixture, "mutate_inode", broken)
    with pytest.raises(TimeoutError):
        fixture.inject(e.args)
    with pytest.raises(FileExistsError):
        fixture.inject(e.args)
    assert len(writes) == 1
    value = e.observer.fault_checkpoint(e.args.output)
    assert value["phase"] == "mutation_unknown" and value["executed"] is False
    assert "SECRET" not in Path(e.args.output).read_text()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux config mutation")
def test_restore_refuses_third_party_edit_or_replaced_instance(environment, monkeypatch):
    e = environment
    fixture.inject(e.args)
    restore_args = SimpleNamespace(config=e.args.config, fault=e.args.output, execute_restore=True)
    e.live.write_text('{"unexpected":3}')
    with pytest.raises(ValueError, match="unexpected current"):
        fixture.restore(restore_args)
    e.live.write_bytes(e.second)
    monkeypatch.setattr(fixture, "same_instance", lambda _: (_ for _ in ()).throw(ValueError("changed instance")))
    with pytest.raises(ValueError, match="changed instance"):
        fixture.restore(restore_args)
    assert e.live.read_bytes() == e.second


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux inode protection")
def test_mutation_refuses_a_different_host_inode(environment, tmp_path, monkeypatch):
    other = tmp_path / "wrong-view.json"; other.write_bytes(environment.first)
    monkeypatch.setattr(fixture, "view_path", lambda _: other)
    with pytest.raises(ValueError, match="not the file bound"):
        fixture.mutate_inode(environment.live, environment.target, environment.args.original_sha256, environment.second)
    assert environment.live.read_bytes() == environment.first


def test_explicit_flags_and_digests_required_before_mutation(environment):
    environment.args.execute_fault = False
    with pytest.raises(ValueError, match="execute-fault"):
        fixture.inject(environment.args)
    environment.args.execute_fault = True
    environment.args.original_sha256 = "0" * 64
    with pytest.raises(ValueError, match="digest differs"):
        fixture.inject(environment.args)
    assert not Path(environment.args.output).exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux mutation journal; Docker command is a stub")
def test_restart_targets_one_checked_container_and_is_not_pure_listener_change(environment, monkeypatch):
    e = environment; e.args.event = "same-container-restart"
    before = {key: e.target[key] for key in ("container_id", "launch_id", "workload_id", "image_config_digest")}
    calls = []
    def inspect(*args):
        return dict(before, running=True, started_at="after" if calls else "before")
    monkeypatch.setattr(fixture, "inspect", inspect)
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(fixture.subprocess, "run", run)
    record = fixture.inject(e.args)
    assert calls == [["docker", "restart", "--time", "0", "c" * 64]]
    assert record["executed"] is True and "not isolated port change" in record["scope"]
    assert 0 < record["started_monotonic_ns"] <= time.monotonic_ns()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux flock")
def test_single_operation_journal_lock(environment):
    first = fixture.Journal(environment.args.output)
    first.save({"type": "fault"})
    try:
        with pytest.raises(BlockingIOError):
            fixture.Journal(environment.args.output, existing=True)
    finally:
        first.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux inode and checkpoint semantics")
def test_hardlinked_backup_or_truncated_restore_journal_is_rejected(environment, tmp_path):
    e = environment
    e.original.unlink()
    os.link(e.live, e.original)
    with pytest.raises(ValueError, match="alias the live config inode"):
        fixture.inject(e.args)
    e.original.unlink(); e.original.write_bytes(e.first)
    fixture.inject(e.args)
    with Path(e.args.output).open("ab") as out:
        out.write(b'{"type":"fault"')
    with pytest.raises(ValueError, match="truncated"):
        fixture.restore(SimpleNamespace(config=e.args.config, fault=e.args.output, execute_restore=True))
    assert e.live.read_bytes() == e.second


@pytest.mark.skipif(sys.platform != "linux", reason="Linux protected operator files")
def test_production_configuration_and_unprotected_fixture_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="beneath /srv/argus-experiments"):
        fixture.protected("/etc/passwd", experiment=True)
    candidate = tmp_path / "unprotected.json"
    candidate.write_text("{}")
    candidate.chmod(0o666)
    with pytest.raises(ValueError, match="root-owned regular file"):
        fixture.protected(candidate)
