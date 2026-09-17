import importlib.util
import json
from pathlib import Path
import pytest
import stat
import sys
from types import SimpleNamespace

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
    cmd = profile.docker_command("docker", {"config_host_path": "/srv/ov.conf", "data_host_path": "/srv/ov-data", "config_path": "/etc/memory/ov.conf", "data_path": "/var/lib/memory", "published_port": 3943, "tls_port": 2943})
    assert "--read-only" in cmd and "--network=bridge" in cmd and "--publish=3943:2943" in cmd
    assert not any(x in " ".join(cmd) for x in ("--privileged", "--network=host", "1933:1933", "/dev/tdx_guest", "spire", "docker.sock"))
    assert any("ov.conf" in arg and "readonly" in arg for arg in cmd)
    assert profile.profile_settings({}, "other") is None
    with pytest.raises(ValueError):
        profile.profile_settings({"workload_attestation_profile": profile.PROFILE}, "other")


@pytest.mark.skipif(sys.platform != "linux", reason="root-owned Linux operator configuration")
def test_configured_ports_paths_and_openviking_constraints(tmp_path, monkeypatch, root_owned_config):
    app = tmp_path / "ov.conf"
    data = tmp_path / "data"
    data.mkdir()
    app.write_text(json.dumps({"server": {"host": "127.0.0.1", "port": 2933}, "storage": {"workspace": "/var/lib/memory"}}))
    w = {"id": "memory-prod", "config_host_path": str(app), "data_host_path": str(data), "config_path": "/etc/memory/ov.conf",
         "data_path": "/var/lib/memory", "listen_port": 2933, "tls_port": 2943, "published_port": 3943}
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({"schema_version": 1, "workload": w}))
    path.chmod(0o600)
    root_owned_config(path)
    monkeypatch.setenv("ARGUS_WORKLOAD_CONFIG", str(path))
    metadata = {"workload_attestation_profile": profile.PROFILE}
    settings = profile.profile_settings(metadata, "memory-prod")
    assert settings == w
    projection = profile.security_projection(settings, "launch-1", "memory-prod")
    command = profile.docker_command("docker", settings)
    assert "--publish=" + projection["published_ports"][0] in command
    assert "OPENVIKING_CONFIG_FILE=/etc/memory/ov.conf" in command
    for key, value in (("listen_port", 1933), ("data_path", "/var/lib/wrong"), ("tls_port", 2933),
                       ("config_path", "/var/lib/memory/ov.conf"), ("config_path", "//etc/memory/ov.conf"),
                       ("published_port", True)):
        path.write_text(json.dumps({"schema_version": 1, "workload": {**w, key: value}}))
        with pytest.raises(ValueError):
            profile.profile_settings(metadata, "memory-prod")
    monkeypatch.delenv("ARGUS_WORKLOAD_CONFIG")
    monkeypatch.setenv("ARGUS_OPENVIKING_CONFIG_PATH", str(app))
    with pytest.raises(ValueError, match="ARGUS_WORKLOAD_CONFIG"):
        profile.profile_settings(metadata, "memory-prod")


@pytest.mark.parametrize("uid,mode,size", [
    (1000, stat.S_IFREG | 0o600, 1),
    (0, stat.S_IFREG | 0o620, 1),
    (0, stat.S_IFREG | 0o602, 1),
    (0, stat.S_IFLNK | 0o777, 1),
    (0, stat.S_IFREG | 0o600, 65537),
])
def test_operator_config_rejects_unprotected_files(monkeypatch, uid, mode, size):
    monkeypatch.setenv("ARGUS_WORKLOAD_CONFIG", "/operator/workload.json")
    original = Path.lstat
    def lstat(path):
        if path == Path("/operator/workload.json"):
            return SimpleNamespace(st_uid=uid, st_mode=mode, st_size=size)
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="protected root-owned file"):
        profile.profile_settings({"workload_attestation_profile": profile.PROFILE}, "memory-prod")
