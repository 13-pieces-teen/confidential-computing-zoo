#!/usr/bin/env python3
"""Company-host lifecycle. No mock evidence, automatic policy approval, or Rekor gate."""
import argparse
from deployment import Deployment, PROFILE, SERVICE_UNITS, render_services
import calendar
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import time
import tempfile
import uuid
from launch_state import execute as execute_launch, operation_lock, digest
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, ProxyHandler

PACKAGE = Path(__file__).resolve().parents[1]
UNITS = list(SERVICE_UNITS.values())  # compatibility for existing diagnostic tools


def run(argv, timeout=30, check=True):
    r = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} failed ({r.returncode}): {r.stderr.strip()}")
    return r.stdout.strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if sys.platform == "linux":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        tmp.unlink(missing_ok=True)


def protected_file(path):
    p = Path(path)
    s = p.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise ValueError(f"{p} must be a root-owned file without group/other write")
    return p


def baseline(c):
    a = c["approved"]
    for f in ("image_config_digest", "config_digest"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", a.get(f, "")):
            raise ValueError(f"missing approved.{f}; use an approved content digest")
    for f in ("mr_td", "rtmr_0", "rtmr_1", "rtmr2_baseline"):
        if not re.fullmatch(r"[0-9a-f]{96}", a.get(f, "")):
            raise ValueError(f"missing approved.{f}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", a.get("policy_id", "")):
        raise ValueError("invalid approved.policy_id")
    if not a.get("executable", "").startswith("/"):
        raise ValueError("approved.executable is required")
    return a


def render_policy(a):
    text = (PACKAGE / "policy/workload_cpu.rego.tmpl").read_text()
    for k, v in a.items():
        text = text.replace("@" + k.upper() + "@", json.dumps(v))
    if re.search(r"@[A-Z0-9_]+@", text):
        raise ValueError("incomplete workload policy")
    return text


def policy_bytes(c):
    """Default remains strict. An explicit reviewed artifact is byte/hash pinned.

    This does not infer a TCB exception from the policy ID or edit any Rego.
    The operator supplies the exact policy already reviewed on IP1.
    """
    baseline(c)
    artifact = c.get("approved_policy_artifact")
    if artifact is None:
        return render_policy(Deployment(c).policy_values()).encode()
    if (not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}
            or not re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", ""))
            or not Path(artifact.get("path", "")).is_absolute()):
        raise ValueError("approved_policy_artifact requires absolute path and explicit SHA-256")
    contents = protected_file(artifact["path"]).read_bytes()
    if not contents or len(contents) > 65536 or hashlib.sha256(contents).hexdigest() != artifact["sha256"]:
        raise ValueError("approved policy artifact is empty, oversized or SHA-256 differs")
    contents.decode("utf-8")
    return contents


def render(c):
    d = Deployment(c)
    a = baseline(c)
    for unit in UNITS:
        if run(["systemctl", "is-active", unit], check=False) in ("active", "activating", "reloading", "deactivating"):
            raise ValueError(f"stop {unit} before applying deployment configuration")
    d.etc.mkdir(parents=True, exist_ok=True, mode=0o700)
    policy = policy_bytes(c)
    (d.etc / (a["policy_id"] + "_cpu.rego")).write_bytes(policy)
    plugin = {
        "evidence_endpoint": "unix://" + d.provider_socket.as_posix(),
        "target_registration_path": d.target.as_posix(),
        "agent_id": d.identity["agent_id"],
        "trustee_endpoint": c["trustee_url"],
        **{k: c[k] for k in ("trustee_ca_path", "trustee_server_name", "ear_public_key_path", "ear_expected_issuer", "ear_expected_profile")},
        "workload_id": d.workload["id"],
        **{k: a[k] for k in ("policy_id", "image_config_digest", "config_digest")},
        "request_timeout": f"{d.request_timeout_seconds}s",
    }
    hcl = "\n".join(f"            {k} = {json.dumps(v)}" for k, v in plugin.items())
    overlay = '''agent {
    socket_path = "@AGENT_SOCKET@"
    experimental {
        broker {
            socket_path = "@BROKER_SOCKET@"
            brokers = [{
                id = "@HELPER_ID@"
                allowed_reference_types = [{ type_url = "type.googleapis.com/spiffe.broker.WorkloadPIDReference" }]
            }]
        }
    }
}
plugins {
    WorkloadAttestor "unix" {
        plugin_data { discover_workload_path = true workload_size_limit = 268435456 }
    }
    WorkloadAttestor "argus_tdx" {
        plugin_cmd = @WORKLOAD_PLUGIN@
        plugin_data {
''' + hcl + '''
        }
    }
}
'''
    overlay = d.render(overlay.replace("@WORKLOAD_PLUGIN@", json.dumps((d.bin / "argus-tdx-workloadattestor").as_posix())))
    (d.etc / "agent-overlay.conf").write_text(overlay)
    protected_file(c["node_agent_config"])
    run([d.bin / "argus-agent-config", "-source", c["node_agent_config"], "-overlay", d.etc / "agent-overlay.conf", "-node-binary", d.bin / "argus-tdx-nodeattestor-agent", "-output", d.etc / "agent.conf", "-trust-domain", d.identity["trust_domain"], "-agent-id", d.identity["agent_id"], "-evidence-socket", d.provider_socket])
    render_services(c, PACKAGE)
    for unit in (d.etc / "systemd").glob("*.service"):
        run(["install", "-m", "0644", unit, Path("/etc/systemd/system") / unit.name])
    run(["systemctl", "daemon-reload"])
    for f in d.etc.glob("*.conf"):
        f.chmod(0o600)
    return {"rendered": str(d.etc), "policy_sha256": hashlib.sha256(policy).hexdigest(),
            "policy_source": "reviewed-artifact" if c.get("approved_policy_artifact") else "strict-template"}


def binary_version(binary):
    # Official SPIRE writes -version to stderr via its CLI UI.
    result = subprocess.run([str(binary), "-version"], capture_output=True, text=True, timeout=10, check=True)
    version = (result.stdout + result.stderr).strip()
    if version != "1.15.3":
        raise ValueError(f"{binary}: expected 1.15.3, got {version}")
    return version


def id_string(value):
    if isinstance(value, str):
        return value
    return "spiffe://" + value.get("trust_domain", value.get("trustDomain", "")) + value.get("path", "")


def selectors(c, identity):
    d = Deployment(c)
    if identity == d.identity["helper_id"]:
        digest = hashlib.sha256((d.bin / "spiffe-helper").read_bytes()).hexdigest()
        return {"unix:uid:0", "unix:path:" + (d.bin / "spiffe-helper").as_posix(), "unix:sha256:" + digest}
    a = baseline(c)
    return {"argus_tdx:verified:true", "argus_tdx:workload_id:" + d.workload["id"],
            "argus_tdx:policy:" + a["policy_id"], "argus_tdx:agent_id:" + d.identity["agent_id"],
            "argus_tdx:image_config_digest:" + a["image_config_digest"],
            "argus_tdx:config_digest:" + a["config_digest"]}


def audit_entries(c, entries, required, identity):
    d = Deployment(c)
    if not entries:
        raise ValueError(f"no Entry for {identity}")
    for e in entries:
        have = {s["type"] + ":" + s["value"] for s in e.get("selectors", [])}
        parent = e.get("parent_id", e.get("parentId", {}))
        sid = e.get("spiffe_id", e.get("spiffeId", {}))
        attr = e.get("additional_attributes", e.get("additionalAttributes", {}))
        prefetch_off = attr.get("disable_x509_svid_prefetch", attr.get("disableX509SvidPrefetch", False))
        if id_string(sid) != identity or id_string(parent) != d.identity["agent_id"] or not required.issubset(have):
            raise ValueError(f"bypass Entry {e.get('id')} for {identity}; review/remove it before starting")
        if e.get("admin") or e.get("downstream") or e.get("store_svid", e.get("storeSvid")):
            raise ValueError(f"unexpected privileges/storage on Entry {e.get('id')}")
        if identity == d.identity["target_id"] and not prefetch_off:
            raise ValueError(f"target Entry {e.get('id')} must disable X509 SVID prefetch")


def server_entries(c, identity):
    d = Deployment(c)
    raw = json.loads(run([d.spire / "spire-server", "entry", "show", "-socketPath", c["server_socket"], "-spiffeID", identity, "-output", "json"]))
    return raw.get("entries", [])


def entry_contract(c):
    d = Deployment(c)
    return {"parent_id": d.identity["agent_id"], "selectors": {
        identity: sorted(selectors(c, identity))
        for identity in (d.identity["helper_id"], d.identity["target_id"])}}


def server_check(c, apply=False):
    d = Deployment(c)
    pid = run(["systemctl", "show", c["server_unit"], "--property=MainPID", "--value"])
    if not pid.isdigit() or int(pid) <= 0:
        raise ValueError("SPIRE Server is not running")
    # Read the running executable, not just the version at the intended install path.
    version = binary_version(Path("/proc") / pid / "exe")
    binary_version(d.spire / "spire-server")
    contract = entry_contract(c)
    result = {"server_version": version, "server_pid": int(pid), "entries": {}, "entry_contract": contract}
    for identity in (d.identity["helper_id"], d.identity["target_id"]):
        required = set(contract["selectors"][identity])
        entries = server_entries(c, identity)
        if not entries and apply:
            cmd = [d.spire / "spire-server", "entry", "create", "-socketPath", c["server_socket"],
                   "-parentID", d.identity["agent_id"], "-spiffeID", identity, "-x509SVIDTTL", "300"]
            if identity == d.identity["target_id"]:
                cmd += ["-disableX509SVIDPrefetch"]
            for selector in sorted(required):
                cmd += ["-selector", selector]
            run(cmd)
            entries = server_entries(c, identity)
        audit_entries(c, entries, required, identity)
        result["entries"][identity] = [e["id"] for e in entries]
    return result


def remote_check(c):
    alias = c.get("server_ssh", "")
    if not alias:
        return server_check(c)
    if alias.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@-]+", alias):
        raise ValueError("server_ssh must be one configured SSH host alias")
    expected = entry_contract(c)
    command = shlex.join(["python3", c["server_script"], "server-check", "--config", c["server_config"]])
    result = json.loads(run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", alias, command]))
    if not isinstance(result, dict) or result.get("entry_contract") != expected:
        raise ValueError("Server Entry contract differs from local deployment identities, baseline or Helper path/binary")
    return result


def direct_trustee(c):
    parsed = urlsplit(c["trustee_url"])
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("trustee_url must be a direct HTTPS origin")
    protected_file(c["trustee_ca_path"])
    protected_file(c["ear_public_key_path"])
    # No HTTP(S)_PROXY or TC API relay: raw TCP from this TDVM to Trustee.
    ctx = ssl.create_default_context(cafile=c["trustee_ca_path"])
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    raw = socket.create_connection((parsed.hostname, parsed.port or 443), timeout=10)
    with ctx.wrap_socket(raw, server_hostname=c["trustee_server_name"]) as channel:
        host = parsed.netloc
        channel.sendall(f"POST /attestation HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}".encode())
        response = http.client.HTTPResponse(channel)
        response.begin()
        if response.status not in (400, 422):
            raise ValueError(f"direct Trustee /attestation schema probe returned HTTP {response.status}; expected 400/422")
    raw = socket.create_connection((parsed.hostname, parsed.port or 443), timeout=10)
    with ctx.wrap_socket(raw, server_hostname=c["trustee_server_name"]) as channel:
        policy_id = baseline(c)["policy_id"]
        channel.sendall(f"GET /policy/{policy_id}_cpu HTTP/1.1\r\nHost: {parsed.netloc}\r\nConnection: close\r\n\r\n".encode())
        response = http.client.HTTPResponse(channel)
        response.begin()
        contents = response.read(65537)
        if response.status != 200 or contents != policy_bytes(c):
            raise ValueError("Trustee workload policy is missing or differs from the rendered approved policy")
    key = run(["openssl", "pkey", "-pubin", "-in", c["ear_public_key_path"], "-text", "-noout"])
    if "prime256v1" not in key and "P-256" not in key:
        raise ValueError("EAR key must be fixed P-256 public key")
    return "HTTPS_AND_ATTESTATION_ROUTE_PASS"


def preflight(c, client_mode="local"):
    d = Deployment(c)
    baseline(c)
    for tool in ("docker", "nsenter", "systemctl", "openssl", "timeout"):
        if not shutil.which(tool):
            raise ValueError(f"missing command: {tool}")
    result = {"agent_binary_version": binary_version(d.spire / "spire-agent"),
              "server": remote_check(c), "trustee_direct": direct_trustee(c)}
    for executable in ("spiffe-helper", "argus-agent-config", "argus-workload", "spiffe-authz", "spiffe-mtls-probe", "argus-tdx-workloadattestor", "argus-spire-evidence-provider"):
        if not os.access(d.bin / executable, os.X_OK):
            raise ValueError(f"missing executable: {d.bin / executable}")
    if run([d.bin / "spiffe-helper", "-version"]) != "0.11.0-argus.1":
        raise ValueError("expected maintained official Helper v0.11.0-argus.1")
    if not Path("/sys/kernel/config/tsm/report").is_dir():
        raise ValueError("Linux TSM report interface is missing; real TDX is required")
    t = json.loads(run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
    for k in ("policy_id", "image_config_digest", "config_digest", "executable"):
        if t[k] != c["approved"][k]:
            raise ValueError(f"registered target differs from approved {k}")
    expected = {"agent_id": d.identity["agent_id"], "workload_id": d.workload["id"],
                "config_path": d.workload["config_path"], "listen_port": str(d.workload["listen_port"])}
    if any(t[k] != value for k, value in expected.items()):
        raise ValueError("registered target differs from deployment identity/configuration")
    # Remote client verification keeps the client SVID/key on the client host;
    # every other target binding, version, Entry, Trustee, policy and readiness
    # check remains. Local mode keeps the original strict credential checks.
    if client_mode == "remote":
        result["client_credentials"] = "REMOTE_CLIENT_VERIFICATION"
    elif client_mode == "local":
        for k in ("client_cert", "client_key", "client_bundle"):
            protected_file(c[k])
        result["client_credentials"] = "LOCAL_VERIFICATION"
    else:
        raise ValueError("client_mode must be local or remote")
    # Check existing Agent version too when this stack is already running.
    pid = run(["systemctl", "show", "argus-workload-agent", "--property=MainPID", "--value"], check=False)
    if pid.isdigit() and int(pid) > 0:
        result["running_agent_version"] = binary_version(Path("/proc") / pid / "exe")
    result.update({"target": t, "rekor_gate": "DEFERRED", "periodic_reattestation": "DEFERRED"})
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Both the authorization header and launch body contain credentials.
        # Require the configured TC API origin to answer the original request.
        return None


def tc_request(c, route, data=None):
    root = urlsplit(c["tc_api_url"])
    if root.scheme != "https" and not (root.scheme == "http" and root.hostname in ("localhost", "127.0.0.1")):
        raise ValueError("TC API must use HTTPS or local loopback HTTP")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("TC_API_BEARER_TOKEN") or os.environ.get("TC_API_IDENTITY_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(c["tc_api_url"].rstrip("/") + route, data=None if data is None else json.dumps(data).encode(), headers=headers)
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
        return json.load(response)


def launch(c):
    d = Deployment(c)
    return execute_launch(d, tc_request, write_json)


def resume_launch(c, launch_id=None):
    return execute_launch(Deployment(c), tc_request, write_json, resume=True, launch_id=launch_id)


def runtime_manifest(c):
    """Content correlation, not a new attestation or a signed security receipt."""
    d = Deployment(c)
    result = {"schema_version": 1, "config_sha256": digest(c), "identity": d.identity,
              "workload_id": d.workload["id"], "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "remote_acceptance": "NOT_RUN", "build_integrity": "NOT_AVAILABLE"}
    policy = d.etc / (c["approved"]["policy_id"] + "_cpu.rego")
    if policy.is_file():
        result["policy_sha256"] = hashlib.sha256(protected_file(policy).read_bytes()).hexdigest()
    manifest_path = d.install / "build-manifest.json"
    if manifest_path.is_file():
        manifest_bytes = protected_file(manifest_path).read_bytes()
        manifest = json.loads(manifest_bytes)
        result["build_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        result["source_revision"] = manifest.get("source_revision")
        result["tracked_patch_sha256"] = manifest.get("tracked_patch_sha256")
        observed = {}
        for name in manifest.get("artifacts", {}):
            relative = Path(name)
            if len(relative.parts) == 2 and relative.parts[0] == "bin":
                path = d.bin / relative.name
            elif len(relative.parts) == 3 and relative.parts[:2] == ("spire-1.15.3", "bin"):
                path = d.spire / relative.name
            else:
                raise ValueError("invalid installed build artifact path")
            observed[name] = hashlib.sha256(protected_file(path).read_bytes()).hexdigest() if path.exists() else None
        result["binaries_sha256"] = observed
        sources = {}
        for name in manifest.get("installed_sources", {}):
            relative = Path(name)
            if len(relative.parts) != 2 or relative.parts[0] not in ("scripts", "config", "policy", "systemd") or relative.name in (".", ".."):
                raise ValueError("invalid installed source path")
            path = d.install / relative
            sources[name] = hashlib.sha256(protected_file(path).read_bytes()).hexdigest() if path.exists() else None
        result["installed_sources_sha256"] = sources
        result["binary_integrity"] = "MATCH" if observed and observed == manifest.get("artifacts") else "MISMATCH"
        result["installed_source_integrity"] = "MATCH" if sources and sources == manifest.get("installed_sources") else "MISMATCH"
        result["build_integrity"] = "MATCH" if manifest.get("schema_version") == 1 and result["binary_integrity"] == result["installed_source_integrity"] == "MATCH" else "MISMATCH"
    for name in ("launch-state", "verify"):
        path = d.records / (name + ".json")
        if path.is_file():
            record = json.loads(protected_file(path).read_text())
            if name == "launch-state":
                result["launch"] = {k: record.get(k) for k in ("run_id", "launch_id", "container_id", "stage", "config_sha256")}
            else:
                result["verification_record_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                result["verification_checked_at"] = record.get("checked_at")
    if d.target.is_file():
        result["registered_target"] = json.loads(protected_file(d.target).read_text())
    return result


def register(c):
    d = Deployment(c)
    info = json.loads(protected_file(d.run / "launch.json").read_text())
    t = json.loads(run([d.bin / "argus-workload", "-action", "register", "-container", info["container"]["container_ID"], "-policy", baseline(c)["policy_id"], "-registration", d.target, "-agent-id", d.identity["agent_id"], "-workload-id", d.workload["id"], "-config", d.workload["config_path"], "-port", str(d.workload["listen_port"])]))
    if t["launch_id"] != info["launch_id"]:
        (d.run / "target.json").unlink()
        raise ValueError("registered launch differs from TC API response")
    return t


def start(c, client_mode="local"):
    record = preflight(c, client_mode)
    record["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Do not reuse a socket or Agent data directory owned by a running process.
    for name in ("spire-agent", "argus-spire-evidence-provider"):
        running = run(["pgrep", "-f", "(^|/)" + name + "( |$)"], check=False)
        if running:
            raise ValueError(f"{name} is still running with PID(s) {running}; stop it before start")
    render(c)
    run(["systemctl", "start", Deployment(c).unit("helper")])
    deadline = time.monotonic() + Deployment(c).startup_timeout_seconds + 5
    while time.monotonic() < deadline:
        s = status(c)
        if s["ready"]:
            record.update(s)
            return record
        time.sleep(1)
    run(["systemctl", "stop", Deployment(c).unit("helper")])
    raise TimeoutError("target identity did not become ready; inspect journalctl -u argus-helper -u argus-workload-agent -u argus-tdx-provider")


def status(c):
    d = Deployment(c)
    units = {u: run(["systemctl", "is-active", u], check=False) for u in d.units.values()}
    serial, invocation, reason = None, None, None
    try:
        invocation = run(["systemctl", "show", d.unit("helper"), "--property=InvocationID", "--value"])
        receipt = json.loads(protected_file(d.credentials / "ready").read_text())
        target = json.loads(protected_file(d.target).read_text())
        if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
            raise ValueError("legacy or unsupported readiness")
        if not re.fullmatch(r"[0-9a-f]{32}", invocation) or receipt.get("invocation_id") != invocation:
            raise ValueError("readiness belongs to another Helper invocation")
        if not isinstance(target, dict) or receipt.get("target") != target or \
                target.get("agent_id") != d.identity["agent_id"] or target.get("workload_id") != d.workload["id"]:
            raise ValueError("readiness target differs from registration/deployment")
        if not isinstance(receipt.get("serial"), str) or not re.fullmatch(r"[1-9][0-9]*", receipt["serial"]):
            raise ValueError("invalid readiness serial")
        expiry = datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError("readiness credential expired")
        if invocation != run(["systemctl", "show", d.unit("helper"), "--property=InvocationID", "--value"]):
            raise ValueError("Helper restarted during readiness check")
        serial = receipt["serial"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        reason = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else type(error).__name__
    return {"units": units, "ready": all(v == "active" for v in units.values()) and serial is not None,
            "target_serial": serial, "helper_invocation_id": invocation, "readiness_error": reason}


def stop(c):
    d = Deployment(c)
    run(["systemctl", "stop", *d.units.values()])
    (d.run / "target.json").unlink(missing_ok=True)
    if (d.credentials / "ready").exists():
        raise ValueError("readiness was not removed")
    return {"stopped": True, "reregistration_required": True}


def event_fields(message, event):
    match = re.search(r"(?:^|\s)" + re.escape(event) + r"\s+(.+)", message)
    if not match:
        return None
    pairs = re.findall(r'(?:^|\s)([a-z_][a-z0-9_]*)=([^\s"\\]+)(?=\s|"|$)', match[1])
    fields = dict(pairs)
    return fields if len(fields) == len(pairs) else None


def correlated_appraisal(journal, target, serial, invocation):
    # Correlate trusted local operational events, not independently verified
    # Quote/EAR tokens. Invocation and instance fields exclude stale runs;
    # later rotations may refer to the same subscription's accepted appraisal.
    expected = {k: target[k] for k in ("launch_id", "container_id", "pid", "start_time")}
    expected["policy"] = target["policy_id"]
    boot = target["boot_id"].replace("-", "")
    subscribed, appraisal, matched = False, None, None
    for line in journal.splitlines():
        record = json.loads(line)
        message = record.get("MESSAGE")
        if not isinstance(message, str) or record.get("_BOOT_ID") != boot:
            continue
        unit = record.get("_SYSTEMD_UNIT")
        helper = unit == "argus-helper.service" and record.get("_SYSTEMD_INVOCATION_ID") == invocation
        if helper:
            fields = event_fields(message, "workload subscription")
            if fields is not None:
                subscribed = all(fields.get(k) == v for k, v in expected.items())
                appraisal, matched = None, None
        if not subscribed:
            continue
        if unit == "argus-workload-agent.service":
            fields = event_fields(message, "workload EAR accepted")
            if fields and all(fields.get(k) == v for k, v in expected.items()) and \
                    re.fullmatch(r"[A-Za-z0-9_-]{43}", fields.get("nonce", "")) and \
                    re.fullmatch(r"[0-9a-f]{64}", fields.get("ear_sha256", "")):
                appraisal = message
        if helper:
            fields = event_fields(message, "target SVID published")
            if fields and all(fields.get(k) == v for k, v in expected.items()) and fields.get("serial") == serial:
                matched = appraisal
    if matched is None:
        raise ValueError("missing correlated EAR acceptance/SVID publication log for the current Helper invocation and target")
    return matched


def helper_invocation():
    invocation = run(["systemctl", "show", "argus-helper", "--property=InvocationID", "--value"])
    if not re.fullmatch(r"[0-9a-f]{32}", invocation):
        raise ValueError("current Helper invocation is unavailable")
    return invocation


def verify(c):
    d = Deployment(c)
    s = status(c)
    if not s["ready"]:
        raise ValueError("workload is not ready")
    invocation = helper_invocation()
    target = json.loads(run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
    proof = json.loads(run([d.bin / "spiffe-mtls-probe", "-url", c["business_url"], "-cert", c["client_cert"],
                           "-key", c["client_key"], "-bundle", c["client_bundle"], "-server-id", d.identity["target_id"]]))
    if proof["client_spiffe_id"] not in d.allowed_client_ids or proof["server_serial"] != s["target_serial"]:
        raise ValueError("business call did not use the expected client/current target SVID")
    started = json.loads(protected_file(d.records / "start.json").read_text())["started_at"]
    started_epoch = calendar.timegm(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ"))
    journal = run(["journalctl", "-u", "argus-tdx-provider", "-u", "argus-workload-agent", "-u", "argus-helper", "--since", f"@{started_epoch}", "--no-pager", "-o", "json"])
    appraisal = correlated_appraisal(journal, target, proof["server_serial"], invocation)
    if helper_invocation() != invocation or status(c) != s:
        raise ValueError("workload changed during verification; retry with the current instance")
    d.records.mkdir(parents=True, exist_ok=True, mode=0o700)
    (d.records / "last-verify-journal.jsonl").write_text(journal)
    (d.records / "last-verify-journal.jsonl").chmod(0o600)
    # The evidence_kind label describes this verifier's intended deployment.
    # Hardware provenance and the tested policy must be recorded by acceptance;
    # this label alone does not establish a real-TDX result.
    return {"target": target, "svid_and_business": proof, "server": remote_check(c), "appraisal": appraisal,
            "helper_invocation_id": invocation, "evidence_kind": "COMPANY_REAL_TDX_RUN",
            "appraisal_log": str(d.records / "last-verify-journal.jsonl")}


def parse_utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must carry an explicit UTC offset")
    return parsed


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


PROBE_OUTPUT_FIELDS = ("checked_at", "client_spiffe_id", "server_spiffe_id", "server_serial", "http_status", "result")


def probe_request(c, window_seconds=1800):
    """Issue a protected remote-client probe request bound to the current instance.

    The client host executes the exact probe tool with live guest credentials and
    returns non-secret artifacts. This does not execute or claim the local verify.
    """
    if not 300 <= window_seconds <= 86400:
        raise ValueError("probe window must be between 300 and 86400 seconds")
    d = Deployment(c)
    s = status(c)
    if not s["ready"]:
        raise ValueError("remote probe request requires a ready target identity")
    target = json.loads(run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
    state = d.records / "launch-state.json"
    if state.is_file():
        run_id = json.loads(protected_file(state).read_text()).get("run_id")
    else:
        run_id = json.loads(protected_file(d.run / "launch.json").read_text()).get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("launch state does not provide the experiment run ID")
    now = datetime.now(timezone.utc)
    request = {
        "schema_version": 1,
        "client_verification": "REMOTE_CLIENT_VERIFICATION",
        "probe_id": uuid.uuid4().hex,
        "run_id": run_id,
        "issued_at": now.isoformat(),
        "not_before": now.isoformat(),
        "not_after": (now + timedelta(seconds=window_seconds)).isoformat(),
        "window_seconds": window_seconds,
        "service_identity": d.identity["target_id"],
        "business_url": c["business_url"],
        "allowed_client_ids": list(d.allowed_client_ids),
        "probe_tool": {
            "name": "spiffe-mtls-probe",
            "usage": "spiffe-mtls-probe -url <business_url> -cert <client live SVID> -key <client live key> -bundle <client trust bundle> -server-id <service_identity>",
            "required_output_fields": list(PROBE_OUTPUT_FIELDS),
        },
        "return_artifacts": ["probe-output.json", "probe-receipt.json", "SHA256SUMS"],
        "receipt_schema": {
            "schema_version": 1, "probe_id": "<echo of request probe_id>", "run_id": "<echo of request run_id>",
            "collected_at": "<ISO-8601 UTC inside the validation window>",
            "probe_output_sha256": "<SHA-256 of probe-output.json>",
            "server_identity_seen": "<server_spiffe_id from the tool output>",
        },
        "instance": {k: target[k] for k in ("launch_id", "container_id", "pid", "start_time", "boot_id",
                                             "policy_id", "image_config_digest", "config_digest")},
        "target_sha256": file_sha256(d.target),
        "helper_invocation_id": s["helper_invocation_id"],
        "target_serial": s["target_serial"],
    }
    path = d.records / ("probe-request-" + request["probe_id"] + ".json")
    write_json(path, request)
    path.chmod(0o600)
    return request


def probe_verify(c, probe_id, result_dir):
    """Verify the non-secret probe artifacts returned by the remote client host.

    A PASS here is explicitly REMOTE_CLIENT_VERIFICATION: no result, an expired
    result, a wrong client/server identity, a wrong peer serial or a changed
    instance never passes, and the local verify command is not claimed.
    """
    d = Deployment(c)
    request_path = d.records / ("probe-request-" + probe_id + ".json")
    request = json.loads(protected_file(request_path).read_text())
    if request.get("schema_version") != 1 or request.get("probe_id") != probe_id or \
            request.get("client_verification") != "REMOTE_CLIENT_VERIFICATION":
        raise ValueError("probe request is not a valid remote-client verification request")
    directory = Path(result_dir)
    listed = {}
    try:
        lines = (directory / "SHA256SUMS").read_text().splitlines()
    except OSError:
        raise ValueError("returned probe artifacts lack SHA256SUMS")
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if not match:
            raise ValueError("returned SHA256SUMS has an invalid entry")
        if match[2] in listed:
            raise ValueError("returned SHA256SUMS lists a file twice")
        listed[match[2]] = match[1]
    actual = {p.name: p for p in directory.iterdir() if p.is_file() and p.name != "SHA256SUMS"}
    if set(listed) != set(actual):
        raise ValueError("returned probe files do not match the digest manifest")
    for name, digest in listed.items():
        if file_sha256(actual[name]) != digest:
            raise ValueError(f"returned probe file differs from its digest: {name}")
    output = json.loads((directory / "probe-output.json").read_text())
    receipt = json.loads((directory / "probe-receipt.json").read_text())
    now = datetime.now(timezone.utc)
    not_before, not_after = parse_utc(request["not_before"]), parse_utc(request["not_after"])
    if now > not_after:
        raise ValueError("remote probe validation window expired; issue a new probe-request and re-agree the window")
    if not not_before <= parse_utc(output["checked_at"]) <= not_after:
        raise ValueError("remote probe checked_at is outside the agreed validation window")
    if not not_before <= parse_utc(receipt.get("collected_at", "")) <= not_after:
        raise ValueError("remote probe collection time is outside the agreed validation window")
    if receipt.get("schema_version") != 1 or receipt.get("probe_id") != probe_id or \
            receipt.get("run_id") != request["run_id"]:
        raise ValueError("remote probe receipt does not bind this probe request")
    if receipt.get("probe_output_sha256") != file_sha256(directory / "probe-output.json"):
        raise ValueError("remote probe receipt output digest differs")
    if receipt.get("server_identity_seen") != output.get("server_spiffe_id"):
        raise ValueError("remote probe receipt disagrees with the tool output")
    if output.get("client_spiffe_id") not in request["allowed_client_ids"]:
        raise ValueError("remote probe client identity is outside the allowed client identities")
    if output.get("server_spiffe_id") != request["service_identity"]:
        raise ValueError("remote probe observed a different service identity")
    if not isinstance(output.get("http_status"), int) or not 200 <= output["http_status"] <= 299:
        raise ValueError("remote probe HTTP result is not a successful response")
    if output.get("result") != "PASS":
        raise ValueError("remote probe tool did not report PASS")
    s = status(c)
    if not s["ready"]:
        raise ValueError("workload is not ready for remote verification")
    if s["helper_invocation_id"] != request["helper_invocation_id"]:
        raise ValueError("Helper invocation changed since the probe request was issued")
    if s["target_serial"] != request["target_serial"]:
        raise ValueError("target SVID rotated within the validation window; agree a new window, do not adjust old results")
    if output["server_serial"] != s["target_serial"]:
        raise ValueError("remote probe peer serial does not match the current target SVID")
    target = json.loads(run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
    if file_sha256(d.target) != request["target_sha256"]:
        raise ValueError("target instance changed since the probe request was issued")
    if any(target.get(k) != v for k, v in request["instance"].items()):
        raise ValueError("target instance facts changed since the probe request was issued")
    started = json.loads(protected_file(d.records / "start.json").read_text())["started_at"]
    started_epoch = calendar.timegm(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ"))
    journal = run(["journalctl", "-u", "argus-tdx-provider", "-u", "argus-workload-agent", "-u", "argus-helper",
                   "--since", f"@{started_epoch}", "--no-pager", "-o", "json"])
    appraisal = correlated_appraisal(journal, target, output["server_serial"], s["helper_invocation_id"])
    if helper_invocation() != s["helper_invocation_id"] or status(c) != s:
        raise ValueError("workload changed during remote verification; retry with the current instance")
    journal_path = d.records / ("probe-verify-journal-" + probe_id + ".jsonl")
    journal_path.write_text(journal)
    journal_path.chmod(0o600)
    result = {"client_verification": "REMOTE_CLIENT_VERIFICATION",
              "client_verification_label": "远端客户端验证",
              "local_verify_executed": False,
              "probe_id": probe_id, "run_id": request["run_id"],
              "validation_window": {"not_before": request["not_before"], "not_after": request["not_after"]},
              "remote": {k: output[k] for k in PROBE_OUTPUT_FIELDS},
              "returned_artifacts": {"probe_output_sha256": file_sha256(directory / "probe-output.json"),
                                     "probe_receipt_sha256": file_sha256(directory / "probe-receipt.json"),
                                     "digest_manifest": "MATCH"},
              "target": target, "server": remote_check(c), "appraisal": appraisal,
              "helper_invocation_id": s["helper_invocation_id"],
              "evidence_kind": "COMPANY_REAL_TDX_RUN",
              "appraisal_log": str(journal_path),
              "note": "远端客户端验证：客户端在 guest 内以实时凭据执行 spiffe-mtls-probe 并回传非秘密原件；本地 verify 未执行，本结果不声称等价于本地 verify。"}
    write_json(d.records / ("probe-verify-" + probe_id + ".json"), result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["render", "preflight", "launch", "resume-launch", "register", "start", "status", "stop", "verify", "manifest", "server-check", "apply-entries", "probe-request", "probe-verify"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--launch-id", help="known server operation ID; only valid with resume-launch")
    parser.add_argument("--client-mode", choices=("local", "remote"), default="local",
                        help="client verification mode for preflight/start; remote defers the business probe to the client host")
    parser.add_argument("--probe-id", help="probe request ID; required with probe-verify")
    parser.add_argument("--probe-result", help="directory holding returned probe-output.json, probe-receipt.json and SHA256SUMS; required with probe-verify")
    parser.add_argument("--probe-window", type=int, default=1800, help="probe validation window in seconds; only valid with probe-request")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("run this lifecycle tool as root on the Linux company host")
    c = json.loads(protected_file(args.config).read_text())
    d = Deployment(c)
    if args.launch_id is not None and args.action != "resume-launch":
        parser.error("--launch-id requires resume-launch")
    if args.client_mode == "remote" and args.action not in ("preflight", "start"):
        parser.error("--client-mode remote is only valid with preflight or start; remote acceptance uses probe-request/probe-verify")
    if args.action == "probe-request" and not 300 <= args.probe_window <= 86400:
        parser.error("--probe-window must be in [300, 86400] seconds")
    if args.action != "probe-request" and args.probe_window != 1800:
        parser.error("--probe-window is only valid with probe-request")
    if args.probe_id is not None and args.action != "probe-verify":
        parser.error("--probe-id requires probe-verify")
    if args.probe_result is not None and args.action != "probe-verify":
        parser.error("--probe-result requires probe-verify")
    if args.action == "probe-verify":
        if args.probe_id is None or not re.fullmatch(r"[0-9a-f]{32}", args.probe_id):
            parser.error("probe-verify requires --probe-id as 32 lowercase hex characters")
        if args.probe_result is None:
            parser.error("probe-verify requires --probe-result")
    functions = {"render": render, "preflight": lambda c: preflight(c, args.client_mode),
                 "launch": launch, "register": register, "start": lambda c: start(c, args.client_mode),
                 "status": status, "stop": stop, "verify": verify, "server-check": server_check,
                 "resume-launch": lambda c: resume_launch(c, args.launch_id),
                 "probe-request": lambda c: probe_request(c, args.probe_window),
                 "probe-verify": lambda c: probe_verify(c, args.probe_id, args.probe_result),
                 "manifest": runtime_manifest, "apply-entries": lambda c: server_check(c, apply=True)}
    if args.action in ("status", "server-check", "manifest"):
        result = functions[args.action](c)
    else:
        with operation_lock(d.records):
            result = functions[args.action](c)
            result["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            write_json(d.records / (args.action + ".json"), result)
            write_json(d.records / "runtime-manifest.json", runtime_manifest(c))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(f"WORKLOAD_ATTESTATION=FAIL: {error}", file=sys.stderr)
        sys.exit(1)
