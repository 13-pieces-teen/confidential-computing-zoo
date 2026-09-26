from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fault_trial import fault_command


def test_dispatch_preserves_exact_arm_tool_and_explicit_owned_hold():
    c = dict(event="helper-freeze", server_fault_fixture="/tools/fault_fixture.py",
             server_remote_acceptance="/opt/isolated-arm/scripts/remote_acceptance.py", server_deployment="/etc/isolated/config.json",
             run_id="trial", fault_file="/evidence/fault.jsonl")
    argv = fault_command(c)
    assert argv[1:3] == [c["server_remote_acceptance"], "fault"]
    assert "--hold-recovery" in argv
    c.update(event="config-change", fixture={"original":"/srv/argus-experiments/a/original.json",
             "replacement":"/srv/argus-experiments/a/new.json", "original_sha256":"a" * 64, "replacement_sha256":"b" * 64})
    argv = fault_command(c)
    assert argv[1:3] == [c["server_fault_fixture"], "inject"]
    assert argv[argv.index("--replacement-sha256") + 1] == "b" * 64
