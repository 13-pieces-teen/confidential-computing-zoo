#!/usr/bin/env python3
"""Explicit static-mTLS client arm; production deployment has no static flag."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from common import atomic, read, require, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HELPER = ROOT / "core/spire/helpers/spiffe-helper"
sys.path.insert(0, str(ROOT / "adapters/OpenClaw/spiffe_client"))
import deploy
import fleet


def overlay_file(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = output / "static-experiment.go"
    source.write_text((HERE / "static-client/credentials.go.txt").read_text(), encoding="utf-8")
    overlay = output / "go-overlay.json"
    atomic(overlay, {"Replace": {str((HELPER / "pkg/clientcredentials/static_experiment.go").resolve()): str(source)}})
    return overlay


def build(output):
    output = Path(output).resolve()
    require(not output.exists(), "static client build directory must be new")
    with tempfile.TemporaryDirectory(prefix="argus-static-build-") as temporary:
        overlay = overlay_file(temporary)
        output.mkdir(parents=True, mode=0o700)
        binary = output / "argus-static-client"
        env = dict(os.environ, GOOS="linux", GOARCH="amd64")
        subprocess.run(["go", "build", "-trimpath", "-tags", "argus_experiment_static", "-overlay", str(overlay),
                        "-o", str(binary), str(HERE / "static-client/main.go")], cwd=HELPER, env=env, check=True, timeout=300)
        binary.chmod(0o755)
    result = {"schema": "argus.static-client-build.v1", "binary_sha256": sha(binary),
              "sources": {path.relative_to(ROOT).as_posix(): sha(path) for path in (HERE / "static-client/main.go", HERE / "static-client/credentials.go.txt")},
              "credential_source": "DEDICATED_STATIC_EXPERIMENT_CA", "remote_acceptance": "NOT_RUN"}
    # Pin the shared publisher implementation too, not just the overlay entrypoint.
    result["publisher_source_sha256"] = {path.name: sha(path) for path in (HELPER / "pkg/clientcredentials").glob("*.go")}
    atomic(output / "build.json", result)
    return result


def select(config, instance):
    require(set(config) == {"schema", "fleet_config", "binary", "binary_sha256", "instances"}
            and config["schema"] == "argus.static-clients.v1", "invalid static client configuration")
    require(Path(config["fleet_config"]).is_absolute(), "absolute fleet artifact reference required")
    require(isinstance(config["binary"], str) and re.fullmatch(r"/[A-Za-z0-9_./-]+", config["binary"])
            and ".." not in Path(config["binary"]).parts and "%" not in config["binary"], "clean absolute Linux binary path required")
    require(isinstance(config["binary_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", config["binary_sha256"]), "pinned static publisher digest required")
    selected = fleet.select(read(config["fleet_config"]), instance)
    identity = deploy.client_id(selected)
    require("/experiment/" in identity and identity.endswith("/static"), "static credentials require isolated experiment identities")
    require(isinstance(config["instances"], dict) and instance in config["instances"], "static credential references missing for instance")
    material = config["instances"][instance]
    require(set(material) == {"cert", "key", "bundle"}, "static client cert/key/bundle references required")
    for path in material.values():
        require(isinstance(path, str) and re.fullmatch(r"/[A-Za-z0-9_./-]+", path)
                and ".." not in Path(path).parts, "clean absolute credential path required")
    return selected, material


def unit_bytes(c, material, binary):
    layout = deploy.paths(c)
    directory = layout["etc"].as_posix()
    return f'''[Unit]
Description=Argus experiment-only static client credentials ({c['_instance']})

[Service]
Type=simple
User=root
Group={layout['group']}
UMask=0027
ExecStart={binary} -config {directory}/credentials.json -cert {material['cert']} -key {material['key']} -bundle {material['bundle']}
ExecStopPost={binary} -config {directory}/credentials.json -clear
Restart=no
TimeoutStopSec=5s
KillMode=control-group
'''.encode()


def install(config, instance):
    require(sys.platform == "linux" and os.geteuid() == 0, "static client install requires Linux root")
    c, material = select(config, instance)
    layout = deploy.paths(c)
    binary = deploy.protected(config["binary"])
    require(sha(binary) == config["binary_sha256"], "static publisher binary changed")
    for key, path in material.items():
        deploy.protected(path, private=key == "key")
    require(service_state(layout["unit"]) not in ("active", "activating", "deactivating", "reloading"),
            "stop this instance's normal publisher before installing static experiment")
    deploy.protected(layout["etc"] / "credentials.json")
    unit = Path("/etc/systemd/system") / (layout["unit"] + ".service")
    expected = unit_bytes(c, material, str(binary))
    if unit.exists():
        existing = deploy.protected(unit).read_bytes()
        require(existing == expected or "Argus experiment-only static" not in existing.decode(), "another static installation already owns this unit")
        backup = layout["etc"] / "dynamic-publisher.service.original"
        if existing != expected:
            require(not backup.exists(), "static experiment backup already exists; inspect before replacing")
            with backup.open("xb") as stream: stream.write(existing)
            backup.chmod(0o600)
    unit.write_bytes(expected); unit.chmod(0o644)
    atomic(layout["etc"] / "static-experiment.json", {"binary_sha256": config["binary_sha256"],
           "unit_sha256": hashlib.sha256(expected).hexdigest(), "identity": deploy.client_id(c)})
    deploy.run(["systemctl", "daemon-reload"])
    return {"installed": True, "started": False, "identity_source": "DEDICATED_STATIC_EXPERIMENT_CA"}


def inspect(config, instance):
    c, material = select(config, instance)
    layout = deploy.paths(c)
    require(sha(deploy.protected(config["binary"])) == config["binary_sha256"], "static binary changed")
    expected = unit_bytes(c, material, config["binary"])
    unit = layout["unit"] + ".service"
    fragment = Path(deploy.run(["systemctl", "show", unit, "-p", "FragmentPath", "--value"]))
    require(deploy.protected(fragment).read_bytes() == expected, "effective static publisher unit differs")
    require(not deploy.run(["systemctl", "show", unit, "-p", "DropInPaths", "--value"]), "unreviewed static publisher drop-ins")
    return {"result": "PASS", "instance": instance, "identity_source": "DEDICATED_STATIC_EXPERIMENT_CA",
            "binary_sha256": config["binary_sha256"], "unit_sha256": hashlib.sha256(expected).hexdigest()}


def register(config, instance, pid):
    require(sys.platform == "linux" and os.geteuid() == 0, "static client registration requires Linux root")
    c, _ = select(config, instance)
    inspect(config, instance)
    layout = deploy.paths(c)
    target = deploy.check_gateway(c, pid)
    require(service_state(layout["unit"]) not in ("active", "activating"), "publisher already active")
    deploy.run([config["binary"], "-config", layout["etc"] / "credentials.json", "-register-pid", str(pid)])
    deploy.check_gateway(c, pid)
    deploy.run(["systemctl", "start", layout["unit"]])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = deploy.guest_status(c)
        if status["ready"]:
            return {**target, **status, "identity_source": "DEDICATED_STATIC_EXPERIMENT_CA"}
        time.sleep(.1)
    deploy.run(["systemctl", "stop", layout["unit"]])
    raise ValueError("static client lease unavailable; inspect this instance's publisher log")


def restore(config, instance):
    require(sys.platform == "linux" and os.geteuid() == 0, "static client restore requires Linux root")
    c, _ = select(config, instance)
    inspect(config, instance)
    layout = deploy.paths(c)
    deploy.run(["systemctl", "stop", layout["unit"]])
    backup = deploy.protected(layout["etc"] / "dynamic-publisher.service.original")
    unit = Path("/etc/systemd/system") / (layout["unit"] + ".service")
    unit.write_bytes(backup.read_bytes()); unit.chmod(0o644)
    backup.unlink()
    (layout["run"] / "target.json").unlink(missing_ok=True)
    (layout["etc"] / "static-experiment.json").unlink(missing_ok=True)
    deploy.run(["systemctl", "daemon-reload"])
    return {"restored": True, "started": False, "reregistration_required": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    command = sub.add_parser("build"); command.add_argument("--output", required=True)
    for action in ("install", "inspect", "register", "restore"):
        command = sub.add_parser(action)
        command.add_argument("--config", required=True); command.add_argument("--instance", required=True)
        if action == "register": command.add_argument("--pid", type=int, required=True)
    args = parser.parse_args()
    if args.action == "build": result = build(args.output)
    else:
        config = read(deploy.protected(args.config))
        result = register(config, args.instance, args.pid) if args.action == "register" else globals()[args.action](config, args.instance)
    print(json.dumps(result, indent=2))


def service_state(unit):
    process = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
    require(process.returncode in (0, 3, 4), "cannot determine publisher state")
    return process.stdout.strip()


if __name__ == "__main__": main()
