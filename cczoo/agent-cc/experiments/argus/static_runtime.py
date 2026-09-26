"""Copied only into the static-mTLS experiment package, never production."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import time


def install_static(namespace):
    dclass, run = namespace["Deployment"], namespace["run"]
    protected, render_services = namespace["protected_file"], namespace["render_services"]
    package = namespace["PACKAGE"]

    def static_files(c):
        config = json.loads(protected(package / "config/static-credentials.json").read_text())
        if set(config) != {"cert", "key", "bundle"}:
            raise ValueError("static credentials require cert/key/bundle")
        return {key: protected(value) for key, value in config.items()}

    def preflight(c):
        d = dclass(c)
        target = json.loads(run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
        required = {"agent_id": d.identity["agent_id"], "workload_id": d.workload["id"],
                    "config_path": d.workload["config_path"], "listen_port": str(d.workload["listen_port"]),
                    **{k: c["approved"][k] for k in ("policy_id", "image_config_digest", "config_digest", "executable")}}
        if any(target.get(k) != value for k, value in required.items()):
            raise ValueError("static experiment target differs from approved local deployment")
        files = static_files(c)
        sans = run(["openssl", "x509", "-in", files["cert"], "-noout", "-ext", "subjectAltName"])
        if re.findall(r"URI:([^,\s]+)", sans) != [d.identity["target_id"]]:
            raise ValueError("static certificate must name only this experiment's exact target identity")
        run(["openssl", "x509", "-in", files["cert"], "-noout", "-checkend", "1"])
        run(["openssl", "verify", "-CAfile", files["bundle"], files["cert"]])
        return {"target": target, "identity_source": "STATIC_EXPERIMENT_CERTIFICATE",
                "workload_appraisal": "NOT_APPLICABLE", "credential_sha256": {
                    key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in files.items()}}

    def render(c):
        d = dclass(c)
        for role in ("nginx", "authz"):
            if run(["systemctl", "is-active", d.unit(role)], check=False) in ("active", "activating", "reloading", "deactivating"):
                raise ValueError("stop static ingress before changing configuration")
        render_services(c, package)
        for unit in (d.etc / "systemd").glob("*.service"):
            run(["install", "-m", "0644", unit, Path("/etc/systemd/system") / unit.name])
        run(["systemctl", "daemon-reload"])
        return {"rendered": str(d.etc), "identity_source": "STATIC_EXPERIMENT_CERTIFICATE"}

    def status(c):
        d = dclass(c)
        units = {d.unit(role): run(["systemctl", "is-active", d.unit(role)], check=False) for role in ("authz", "nginx")}
        return {"units": units, "ready": all(value == "active" for value in units.values()),
                "identity_source": "STATIC_EXPERIMENT_CERTIFICATE", "workload_appraisal": "NOT_APPLICABLE"}

    def verify(c):
        d = dclass(c)
        record = preflight(c)
        if not status(c)["ready"]:
            raise ValueError("static experiment ingress is not active")
        proof = json.loads(run([d.bin / "spiffe-mtls-probe", "-url", c["business_url"], "-cert", c["client_cert"],
                               "-key", c["client_key"], "-bundle", c["client_bundle"], "-server-id", d.identity["target_id"]]))
        if proof["client_spiffe_id"] not in d.allowed_client_ids:
            raise ValueError("static business probe used an unapproved client")
        serial = run(["openssl", "x509", "-in", d.credentials / "current/svid.pem", "-noout", "-serial"])
        if str(int(serial.split("=", 1)[1], 16)) != proof["server_serial"]:
            raise ValueError("static probe did not use this deployment's certificate")
        return {**record, "svid_and_business": proof, "evidence_kind": "STATIC_MTLS_COST_REFERENCE"}

    def start(c):
        record = preflight(c)
        render(c)
        d = dclass(c)
        current = d.credentials / "current"
        if current.exists() or current.is_symlink():
            raise ValueError("stale static credential generation; explicitly stop before restart")
        current.mkdir(parents=True, mode=0o700)
        for key, name in (("cert", "svid.pem"), ("key", "key.pem"), ("bundle", "bundle.pem")):
            shutil.copyfile(static_files(c)[key], current / name)
            (current / name).chmod(0o600)
        try:
            run(["systemctl", "start", d.unit("authz"), d.unit("nginx")])
            record.update(verify(c))
        except Exception:
            run(["systemctl", "stop", d.unit("nginx"), d.unit("authz")], check=False)
            raise
        record["started_at"] = datetime.now(timezone.utc).isoformat()
        return record

    def stop(c):
        d = dclass(c)
        run(["systemctl", "stop", d.unit("nginx"), d.unit("authz")])
        current = d.credentials / "current"
        if current.is_symlink():
            raise ValueError("unexpected static credential symlink")
        if current.is_dir():
            for name in ("svid.pem", "key.pem", "bundle.pem"):
                (current / name).unlink(missing_ok=True)
            current.rmdir()
        d.target.unlink(missing_ok=True)
        return {"stopped": True, "reregistration_required": True}

    def no_entries(c, apply=False):
        return {"entries": {}, "identity_source": "STATIC_EXPERIMENT_CERTIFICATE"}

    namespace.update(render=render, preflight=preflight, start=start, status=status,
                     verify=verify, stop=stop, server_check=no_entries)
