"""Use the original pinned console entry and wrap only its uvicorn ASGI app."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

from .middleware import ReceiverAudit


def verify_upstream():
    lock = json.loads(Path(__file__).with_name("upstream-lock.json").read_text())
    distribution = importlib.metadata.distribution("openviking")
    if distribution.version != lock["version"]:
        raise RuntimeError("audit adapter requires the pinned OpenViking version")
    entries = [entry for entry in distribution.entry_points
               if entry.group == "console_scripts" and entry.name == "openviking-server"]
    if len(entries) != 1 or entries[0].value != lock["console_entry"]:
        raise RuntimeError("OpenViking console entry changed")
    for name, expected in lock["files"].items():
        path = distribution.locate_file(name)
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise RuntimeError("OpenViking startup file differs from the pinned source: " + name)
    return entries[0]


def wrap_uvicorn(original, socket_path, run_id):
    def run(app, *args, **kwargs):
        if isinstance(app, str) or kwargs.get("workers", 1) != 1 or kwargs.get("factory") or kwargs.get("reload"):
            raise RuntimeError("receiver process binding requires a single ASGI worker without reload")
        return original(ReceiverAudit(app, socket_path, run_id), *args, **kwargs)
    return run


def main():
    entry = verify_upstream()
    if sys.argv[1:] == ["--argus-verify-upstream"]:
        print("pinned OpenViking console entry and startup hashes verified")
        return
    mode = os.environ.get("ARGUS_AUDIT_MODE", "on")
    if mode not in ("on", "off"):
        raise RuntimeError("ARGUS_AUDIT_MODE must be on or explicit overhead-control off")
    if mode == "on":
        socket_path, run_id = os.environ.get("ARGUS_AUDIT_SOCKET", ""), os.environ.get("ARGUS_AUDIT_RUN_ID", "")
        # Validate before any model or OpenViking configuration is imported.
        ReceiverAudit(None, socket_path, run_id)
        import uvicorn
        uvicorn.run = wrap_uvicorn(uvicorn.run, socket_path, run_id)
    print(json.dumps({"argus_receiver_audit": mode, "boundary": "asgi_application_read",
                      "remote_acceptance": "NOT_RUN"}), flush=True)
    # The upstream lightweight entry parses --config before importing OpenViking.
    entry.load()()


if __name__ == "__main__":
    main()
