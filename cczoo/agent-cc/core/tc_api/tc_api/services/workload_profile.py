"""Fixed OpenViking launch profile for host-managed NGINX/SPIFFE Helper.

Paths come from the operator's TC API environment, never request mount strings.
Existing transparency-log submission still records the launch result.
"""
import json
import os
import re
from pathlib import Path

PROFILE = "nginx-spiffe-helper-v1"


def profile_settings(metadata, workload_id):
    name = (metadata or {}).get("workload_attestation_profile")
    if name is None:
        return None
    if name != PROFILE or workload_id != "openviking-cmem":
        raise ValueError("unsupported workload attestation profile or workload")
    paths = {}
    for key in ("ARGUS_OPENVIKING_CONFIG_PATH", "ARGUS_OPENVIKING_DATA_PATH"):
        value = os.environ.get(key, "")
        if not value.startswith("/") or any(ch in value for ch in ",\r\n"):
            raise ValueError(f"{key} must be an absolute Docker-host path without commas")
        paths[key] = value
    config = Path(paths["ARGUS_OPENVIKING_CONFIG_PATH"])
    if not config.is_file() or config.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("OpenViking configuration is missing or too large")
    data = json.loads(config.read_text(encoding="utf-8"))
    if data.get("server", {}).get("host") != "127.0.0.1" or data.get("server", {}).get("port") != 1933:
        raise ValueError("OpenViking must listen on 127.0.0.1:1933")
    if data.get("storage", {}).get("workspace") != "/var/lib/openviking":
        raise ValueError("OpenViking storage.workspace must be /var/lib/openviking")
    if not Path(paths["ARGUS_OPENVIKING_DATA_PATH"]).is_dir():
        raise ValueError("OpenViking data directory is missing")
    return paths


def docker_command(binary, settings):
    return [
        binary, "run", "-d", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--network=bridge",
        "--publish=1943:1943",
        "--mount", f"type=bind,source={settings['ARGUS_OPENVIKING_CONFIG_PATH']},target=/etc/openviking/ov.conf,readonly",
        "--mount", f"type=bind,source={settings['ARGUS_OPENVIKING_DATA_PATH']},target=/var/lib/openviking",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
        "--env", "OPENVIKING_CONFIG_FILE=/etc/openviking/ov.conf",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "OPENVIKING_WITH_BOT=0",
    ]


def observed_container(container, launch_id, workload_id):
    """Keep actual image content ID separate from legacy log image_digest."""
    image = container.get("Image", "")
    cid = container.get("Id", "")
    labels = container.get("Config", {}).get("Labels") or {}
    state = container.get("State", {})
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image) or not re.fullmatch(r"[0-9a-f]{64}", cid):
        raise ValueError("Docker did not return actual content identifiers")
    if labels.get("io.trucon.launch-id") != launch_id or labels.get("io.trucon.workload-id") != workload_id:
        raise ValueError("container launch labels mismatch")
    if not state.get("Running") or not isinstance(state.get("Pid"), int) or state["Pid"] <= 0:
        raise ValueError("container is not running")
    return {
        "container_ID": cid, "container_Status": state["Status"],
        "launch_id": launch_id, "workload_id": workload_id,
        "runtime_image_config_digest": image,
        "container_init_host_pid": state["Pid"],
        "container_started_at": state["StartedAt"],
        "attestation_profile": PROFILE,
    }
