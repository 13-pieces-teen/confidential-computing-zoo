"""Real launch workflow with substituted Docker/registry/log transports."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import tc_api.api.workflows as workflows
import tc_api.services.launch as launch
import tc_api.utils.registry as registry
from tc_api.models import LaunchRequest
from tc_api.services.workload_profile import PROFILE


@pytest.mark.skipif(sys.platform != "linux", reason="operator Docker paths are Linux paths")
def test_profile_survives_launch_result_and_existing_log_commit(tmp_path, monkeypatch):
    config = tmp_path / "ov.conf"
    config.write_text(json.dumps({"server": {"host": "127.0.0.1", "port": 1933},
                                  "storage": {"workspace": "/var/lib/openviking"}}))
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ARGUS_OPENVIKING_CONFIG_PATH", str(config))
    monkeypatch.setenv("ARGUS_OPENVIKING_DATA_PATH", str(data))
    monkeypatch.setattr(registry, "ALLOWED_EXTERNAL_IMAGE_REGISTRIES", "registry.example")
    container = {"Id": "c" * 64, "Image": "sha256:" + "a" * 64,
                 "Config": {"Labels": {"io.trucon.launch-id": "launch-flow", "io.trucon.workload-id": "openviking-cmem"}},
                 "State": {"Running": True, "Pid": 1234, "Status": "running", "StartedAt": "2026-09-05T00:00:00Z"}}
    commands = []

    def process(cmd, **kwargs):
        commands.append(cmd)
        result = "ok"
        if cmd[1] == "run":
            result = container["Id"]
        elif cmd[1] == "inspect":
            result = "running" if "--format" in cmd else json.dumps([container])
        return SimpleNamespace(returncode=0, stdout=result, stderr="")

    monkeypatch.setattr(launch.subprocess, "run", process)
    monkeypatch.setattr(launch, "WorkloadStore", Mock())
    monkeypatch.setattr(workflows, "WorkloadStore", Mock())
    service = workflows.docker_service
    monkeypatch.setattr(service, "_resolve_image_digest", lambda _: "legacy-log-digest")
    monkeypatch.setattr(service, "pull_image", lambda *a, **kw: True)
    commit = Mock(return_value=(True, "test-log-receipt"))
    monkeypatch.setattr(service, "commit_and_save_receipt", commit)
    monkeypatch.setattr(service, "verify_chain_state", lambda *a, **kw: "success")
    monkeypatch.setattr(service, "update_transparencylog_status", Mock())
    statuses = Mock()
    monkeypatch.setattr(service, "update_launch_status", statuses)
    entries = []
    tlog = SimpleNamespace(add_entry=lambda _, entry: entries.append(entry))
    request = LaunchRequest(image_id="openviking-cmem", image_url="docker://registry.example/openviking:approved",
                            user_id="alice", identity_token="test-identity-token", attestation_required=False,
                            metadata={"workload_id": "openviking-cmem", "workload_attestation_profile": PROFILE})
    asyncio.run(workflows.launch_container_async(request, "launch-flow", "openviking-cmem", "test-chain", str(tmp_path), tlog, "record"))
    result = statuses.call_args.kwargs
    assert result.get("status") == "success", statuses.call_args_list
    observed = result["evidence"]["instance_ids"][0]
    assert observed["runtime_image_config_digest"] == container["Image"]
    assert observed["launch_id"] == "launch-flow" and observed["attestation_profile"] == PROFILE
    commit.assert_called_once_with("launch", "launch-flow", tlog, "record", "test-identity-token")
    logged = next(e.value for e in entries if e.key == "container_info")
    assert logged == observed
    command = next(c for c in commands if c[1] == "run")
    assert "--publish=1943:1943" in command and "--read-only" in command
    assert "--privileged" not in command and "/dev/tdx_guest:/dev/tdx_guest" not in command
    with pytest.raises(ValueError, match="dockercmd overrides"):
        asyncio.run(service.launch_containers(tlog, "record", request.image_url, request.image_id, str(tmp_path),
                                              workload_id="openviking-cmem", metadata=request.metadata, dockercmd="docker run --privileged"))
