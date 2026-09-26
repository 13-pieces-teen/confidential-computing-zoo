"""Configured OpenViking launch profile for host-managed NGINX/SPIFFE Helper.

Paths come from the operator's TC API environment, never request mount strings.
Existing transparency-log submission still records the launch result.
"""
import json
import os
import re
from pathlib import Path, PurePosixPath
import stat

PROFILE = "nginx-spiffe-helper-v1"
TMPFS = "/tmp:rw,noexec,nosuid,size=256m"


def profile_settings(metadata, workload_id):
    name = (metadata or {}).get("workload_attestation_profile")
    if name is None:
        return None
    if name != PROFILE:
        raise ValueError("unsupported workload attestation profile")
    value = os.environ.get("ARGUS_WORKLOAD_CONFIG", "")
    if not value.startswith("/"):
        raise ValueError("ARGUS_WORKLOAD_CONFIG must name the generated absolute configuration path")
    source = Path(value)
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 65536:
        raise ValueError("workload configuration must be a protected root-owned file")
    deployment = json.loads(source.read_text(encoding="utf-8"))
    if set(deployment) not in ({"schema_version", "workload"}, {"schema_version", "workload", "receiver_audit"}) or type(deployment["schema_version"]) is not int or deployment["schema_version"] != 1:
        raise ValueError("unsupported workload configuration schema")
    settings = deployment["workload"]
    keys = {"id", "config_host_path", "data_host_path", "config_path", "data_path", "listen_port", "tls_port", "published_port"}
    if not isinstance(settings, dict) or set(settings) != keys or settings["id"] != workload_id:
        raise ValueError("workload configuration fields or workload ID mismatch")
    for key in ("config_host_path", "data_host_path", "config_path", "data_path"):
        value = settings[key]
        if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or value.startswith("//") or str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts:
            raise ValueError("workload paths must be clean absolute Linux paths")
    if PurePosixPath(settings["config_path"]).is_relative_to(settings["data_path"]) or PurePosixPath(settings["config_host_path"]).is_relative_to(settings["data_host_path"]):
        raise ValueError("configuration must not be inside the writable data directory")
    if any(PurePosixPath(settings[key]).is_relative_to("/tmp") for key in ("config_path", "data_path")):
        raise ValueError("application mounts must remain outside temporary storage")
    for key in ("listen_port", "tls_port", "published_port"):
        if type(settings[key]) is not int or not 1 <= settings[key] <= 65535:
            raise ValueError("invalid workload port")
    if settings["listen_port"] == settings["tls_port"]:
        raise ValueError("business and TLS listener ports must differ")
    config = Path(settings["config_host_path"])
    if not config.is_file() or config.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("OpenViking configuration is missing or too large")
    data = json.loads(config.read_text(encoding="utf-8"))
    if data.get("server", {}).get("host") != "127.0.0.1" or data.get("server", {}).get("port") != settings["listen_port"]:
        raise ValueError("OpenViking must listen on the configured loopback port")
    if data.get("storage", {}).get("workspace") != settings["data_path"]:
        raise ValueError("OpenViking storage.workspace differs from configured data_path")
    if not Path(settings["data_host_path"]).is_dir():
        raise ValueError("OpenViking data directory is missing")
    if "receiver_audit" in deployment:
        audit = deployment["receiver_audit"]
        if not isinstance(audit, dict) or set(audit) != {"run_id", "mode", "image_config_digest"}:
            raise ValueError("invalid receiver audit settings")
        if not isinstance(audit["run_id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", audit["run_id"]) or audit["mode"] not in ("on", "off") or not isinstance(audit["image_config_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", audit["image_config_digest"]):
            raise ValueError("receiver audit requires an explicit run, mode and pinned image digest")
        audit_roots = (PurePosixPath("/run/argus-audit"), PurePosixPath("/run/argus-receiver"))
        if any(PurePosixPath(settings[key]).is_relative_to(root) or root.is_relative_to(settings[key])
               for key in ("config_path", "data_path", "config_host_path", "data_host_path") for root in audit_roots):
            raise ValueError("receiver audit mounts overlap application configuration/data")
        directory = Path(audit_directory(audit))
        for path in [directory] + list(directory.parents):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("receiver audit socket directory chain must be root-owned and non-writable")
        settings = {**settings, "receiver_audit": audit}
    return settings


def audit_directory(audit):
    return PurePosixPath("/run/argus-receiver") / audit["run_id"] / "data"


def assert_audit_image(settings, actual_image_id):
    audit = settings.get("receiver_audit")
    if audit and actual_image_id != audit["image_config_digest"]:
        raise ValueError("refusing to launch unapproved image with receiver audit socket access")


def launch_environment(settings):
    result = {"OPENVIKING_CONFIG_FILE": settings["config_path"], "PYTHONDONTWRITEBYTECODE": "1", "OPENVIKING_WITH_BOT": "0"}
    if settings.get("receiver_audit"):
        audit = settings["receiver_audit"]
        result.update(ARGUS_AUDIT_MODE=audit["mode"], ARGUS_AUDIT_RUN_ID=audit["run_id"],
                      ARGUS_AUDIT_SOCKET="/run/argus-audit/receiver.sock")
    return result


def security_projection(settings, launch_id, workload_id):
    mounts = [f"{settings['config_host_path']}:{settings['config_path']}:ro", f"{settings['data_host_path']}:{settings['data_path']}"]
    if settings.get("receiver_audit"):
        mounts.append(f"{audit_directory(settings['receiver_audit'])}:/run/argus-audit:ro")
    return {"launch_id": launch_id, "workload_id": workload_id, "privileged": False,
            "network_mode": "bridge", "mounts": mounts,
            "devices": [], "capabilities": [], "read_only_rootfs": True,
            "tmpfs": [TMPFS],
            "published_ports": [f"{settings['published_port']}:{settings['tls_port']}"], "attestation_profile": PROFILE,
            "launch_env_keys": sorted(launch_environment(settings))}


def docker_command(binary, settings):
    command = [
        binary, "run", "-d", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--network=bridge",
        f"--publish={settings['published_port']}:{settings['tls_port']}",
        "--mount", f"type=bind,source={settings['config_host_path']},target={settings['config_path']},readonly",
        "--mount", f"type=bind,source={settings['data_host_path']},target={settings['data_path']}",
        "--tmpfs", TMPFS,
    ]
    if settings.get("receiver_audit"):
        command += ["--mount", f"type=bind,source={audit_directory(settings['receiver_audit'])},target=/run/argus-audit,readonly"]
    return command + [arg for key, value in launch_environment(settings).items() for arg in ("--env", f"{key}={value}")]


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
