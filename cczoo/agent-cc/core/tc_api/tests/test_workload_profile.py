import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("workload_profile", Path(__file__).parents[1] / "tc_api/services/workload_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def test_actual_content_id_and_launch_are_retained():
    container = {"Id": "c" * 64, "Image": "sha256:" + "a" * 64,
                 "Config": {"Labels": {"io.trucon.launch-id": "launch-1", "io.trucon.workload-id": "openviking-cmem"}},
                 "State": {"Running": True, "Pid": 1234, "Status": "running", "StartedAt": "2026-09-05T00:00:00Z"}}
    observed = profile.observed_container(container, "launch-1", "openviking-cmem")
    assert observed["runtime_image_config_digest"] == container["Image"]
    assert observed["launch_id"] == "launch-1"
    for update in ({"Image": "openviking:latest"}, {"State": {"Running": False}}, {"Id": "short"}):
        with pytest.raises(ValueError):
            profile.observed_container(container | update, "launch-1", "openviking-cmem")
    with pytest.raises(ValueError):
        profile.observed_container(container, "replacement", "openviking-cmem")


def test_profile_has_private_network_no_quote_device_and_ro_config():
    cmd = profile.docker_command("docker", {"ARGUS_OPENVIKING_CONFIG_PATH": "/srv/ov.conf", "ARGUS_OPENVIKING_DATA_PATH": "/srv/ov-data"})
    assert "--read-only" in cmd and "--network=bridge" in cmd and "--publish=1943:1943" in cmd
    assert not any(x in " ".join(cmd) for x in ("--privileged", "--network=host", "1933:1933", "/dev/tdx_guest", "spire", "docker.sock"))
    assert any("ov.conf" in arg and "readonly" in arg for arg in cmd)
    assert profile.profile_settings({}, "other") is None
    with pytest.raises(ValueError):
        profile.profile_settings({"workload_attestation_profile": profile.PROFILE}, "other")
