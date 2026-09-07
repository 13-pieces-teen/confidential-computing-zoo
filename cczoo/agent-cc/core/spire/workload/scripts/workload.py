#!/usr/bin/env python3
"""Company-host lifecycle. No mock evidence, automatic policy approval, or Rekor gate."""
import argparse
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
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, ProxyHandler

PACKAGE = Path(__file__).resolve().parents[1]
BIN = Path("/opt/argus-workload/bin")
ETC = Path("/etc/argus-workload")
RUN = Path("/run/argus-workload")
RECORDS = Path("/var/log/argus-workload")
SPIRE = Path("/opt/spire-1.15.3/bin")
AGENT_ID = "spiffe://argus.local/spire/agent/argus_tdx/openviking-node"
TARGET_ID = "spiffe://argus.local/service/openviking-cmem"
HELPER_ID = "spiffe://argus.local/infra/openviking-helper"
UNITS = ["argus-helper", "argus-nginx", "argus-authz", "argus-workload-agent", "argus-tdx-provider"]


def run(argv, timeout=30, check=True):
    r = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} failed ({r.returncode}): {r.stderr.strip()}")
    return r.stdout.strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(tmp, path)
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
    for f in ("mr_td", "rtmr_0", "rtmr_1", "rtmr_2"):
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
    if re.search(r"@[A-Z_]+@", text):
        raise ValueError("incomplete workload policy")
    return text


def render(c):
    a = baseline(c)
    ETC.mkdir(parents=True, exist_ok=True, mode=0o700)
    policy = render_policy(a)
    (ETC / (a["policy_id"] + "_cpu.rego")).write_text(policy)
    plugin = {
        "evidence_endpoint": "unix:///run/argus/evidence-provider.sock",
        "target_registration_path": "/run/argus-workload/target.json",
        "trustee_endpoint": c["trustee_url"],
        **{k: c[k] for k in ("trustee_ca_path", "trustee_server_name", "ear_public_key_path", "ear_expected_issuer", "ear_expected_profile")},
        "workload_id": "openviking-cmem",
        **{k: a[k] for k in ("policy_id", "image_config_digest", "config_digest")},
        "request_timeout": "20s",
    }
    hcl = "\n".join(f"            {k} = {json.dumps(v)}" for k, v in plugin.items())
    overlay = '''agent {
    socket_path = "/run/spire/agent/agent.sock"
    experimental {
        broker {
            socket_path = "/run/spire/broker/broker.sock"
            brokers = [{
                id = "spiffe://argus.local/infra/openviking-helper"
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
    overlay = overlay.replace("@WORKLOAD_PLUGIN@", json.dumps(str(BIN / "argus-tdx-workloadattestor")))
    (ETC / "agent-overlay.conf").write_text(overlay)
    protected_file(c["node_agent_config"])
    run([BIN / "argus-agent-config", "-source", c["node_agent_config"], "-overlay", ETC / "agent-overlay.conf", "-node-binary", BIN / "argus-tdx-nodeattestor-agent", "-output", ETC / "agent.conf"])
    for f in ("nginx.conf", "helper.conf"):
        shutil.copyfile(PACKAGE / "config" / f, ETC / f)
    for f in ETC.glob("*.conf"):
        f.chmod(0o600)
    return {"rendered": str(ETC), "policy_sha256": hashlib.sha256(policy.encode()).hexdigest()}


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
    if identity == HELPER_ID:
        digest = hashlib.sha256((BIN / "spiffe-helper").read_bytes()).hexdigest()
        return {"unix:uid:0", "unix:path:/opt/argus-workload/bin/spiffe-helper", "unix:sha256:" + digest}
    a = baseline(c)
    return {"argus_tdx:verified:true", "argus_tdx:workload_id:openviking-cmem",
            "argus_tdx:policy:" + a["policy_id"], "argus_tdx:agent_id:" + AGENT_ID,
            "argus_tdx:image_config_digest:" + a["image_config_digest"],
            "argus_tdx:config_digest:" + a["config_digest"]}


def audit_entries(entries, required, identity):
    if not entries:
        raise ValueError(f"no Entry for {identity}")
    for e in entries:
        have = {s["type"] + ":" + s["value"] for s in e.get("selectors", [])}
        parent = e.get("parent_id", e.get("parentId", {}))
        sid = e.get("spiffe_id", e.get("spiffeId", {}))
        attr = e.get("additional_attributes", e.get("additionalAttributes", {}))
        prefetch_off = attr.get("disable_x509_svid_prefetch", attr.get("disableX509SvidPrefetch", False))
        if id_string(sid) != identity or id_string(parent) != AGENT_ID or not required.issubset(have):
            raise ValueError(f"bypass Entry {e.get('id')} for {identity}; review/remove it before starting")
        if e.get("admin") or e.get("downstream") or e.get("store_svid", e.get("storeSvid")):
            raise ValueError(f"unexpected privileges/storage on Entry {e.get('id')}")
        if identity == TARGET_ID and not prefetch_off:
            raise ValueError(f"target Entry {e.get('id')} must disable X509 SVID prefetch")


def server_entries(c, identity):
    raw = json.loads(run([SPIRE / "spire-server", "entry", "show", "-socketPath", c["server_socket"], "-spiffeID", identity, "-output", "json"]))
    return raw.get("entries", [])


def server_check(c, apply=False):
    pid = run(["systemctl", "show", c["server_unit"], "--property=MainPID", "--value"])
    if not pid.isdigit() or int(pid) <= 0:
        raise ValueError("SPIRE Server is not running")
    # Read the running executable, not just the version at the intended install path.
    version = binary_version(Path("/proc") / pid / "exe")
    binary_version(SPIRE / "spire-server")
    result = {"server_version": version, "server_pid": int(pid), "entries": {}}
    for identity in (HELPER_ID, TARGET_ID):
        required = selectors(c, identity)
        entries = server_entries(c, identity)
        if not entries and apply:
            cmd = [SPIRE / "spire-server", "entry", "create", "-socketPath", c["server_socket"],
                   "-parentID", AGENT_ID, "-spiffeID", identity, "-x509SVIDTTL", "300"]
            if identity == TARGET_ID:
                cmd += ["-disableX509SVIDPrefetch"]
            for selector in sorted(required):
                cmd += ["-selector", selector]
            run(cmd)
            entries = server_entries(c, identity)
        audit_entries(entries, required, identity)
        result["entries"][identity] = [e["id"] for e in entries]
    return result


def remote_check(c):
    alias = c.get("server_ssh", "")
    if not alias:
        return server_check(c)
    if alias.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@-]+", alias):
        raise ValueError("server_ssh must be one configured SSH host alias")
    command = shlex.join(["python3", c["server_script"], "server-check", "--config", c["server_config"]])
    return json.loads(run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", alias, command]))


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
        if response.status != 200 or contents != render_policy(c["approved"]).encode():
            raise ValueError("Trustee workload policy is missing or differs from the rendered approved policy")
    key = run(["openssl", "pkey", "-pubin", "-in", c["ear_public_key_path"], "-text", "-noout"])
    if "prime256v1" not in key and "P-256" not in key:
        raise ValueError("EAR key must be fixed P-256 public key")
    return "HTTPS_AND_ATTESTATION_ROUTE_PASS"


def preflight(c):
    baseline(c)
    for tool in ("docker", "nsenter", "systemctl", "openssl", "timeout"):
        if not shutil.which(tool):
            raise ValueError(f"missing command: {tool}")
    result = {"agent_binary_version": binary_version(SPIRE / "spire-agent"),
              "server": remote_check(c), "trustee_direct": direct_trustee(c)}
    for executable in ("spiffe-helper", "argus-agent-config", "argus-workload", "spiffe-authz", "spiffe-mtls-probe", "argus-tdx-workloadattestor", "argus-tdx-evidence-provider"):
        if not os.access(BIN / executable, os.X_OK):
            raise ValueError(f"missing executable: {BIN / executable}")
    if run([BIN / "spiffe-helper", "-version"]) != "0.11.0-argus.1":
        raise ValueError("expected maintained official Helper v0.11.0-argus.1")
    if not Path("/sys/kernel/config/tsm/report").is_dir():
        raise ValueError("Linux TSM report interface is missing; real TDX is required")
    t = json.loads(run([BIN / "argus-workload", "-action", "check"]))
    for k in ("policy_id", "image_config_digest", "config_digest", "executable"):
        if t[k] != c["approved"][k]:
            raise ValueError(f"registered target differs from approved {k}")
    for k in ("client_cert", "client_key", "client_bundle"):
        protected_file(c[k])
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
    if not os.environ.get("TC_API_IDENTITY_TOKEN"):
        raise ValueError("missing TC_API_IDENTITY_TOKEN for existing transparency-log upload")
    payload = {"image_id": c["image_id"], "image_url": c["image_url"], "user_id": c["tc_api_user_id"],
               "identity_token": os.environ["TC_API_IDENTITY_TOKEN"], "attestation_required": False,
               "metadata": {"workload_id": "openviking-cmem", "service_name": "openviking-cmem", "workload_attestation_profile": "nginx-spiffe-helper-v1"}}
    launch_id = tc_request(c, "/api/deploy-launch", payload)["launch_id"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", launch_id):
        raise ValueError("invalid TC API launch ID")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        result = tc_request(c, "/api/launch-result/" + launch_id)
        if result["status"] == "failed":
            raise ValueError(f"TC API launch {launch_id} failed; inspect its protected launch log")
        if result["status"] == "success":
            instances = (result.get("evidence") or {}).get("instance_ids") or result.get("instance_ids")
            if not isinstance(instances, list) or len(instances) != 1:
                raise ValueError("expected one TC API container")
            info = instances[0]
            if info.get("launch_id") != launch_id or info.get("attestation_profile") != "nginx-spiffe-helper-v1":
                raise ValueError("TC API did not retain the launch/profile association; upgrade TC API")
            RUN.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_json(RUN / "launch.json", {"launch_id": launch_id, "container": info})
            return {"launch_id": launch_id, "container_id": info["container_ID"]}
        time.sleep(2)
    raise TimeoutError(f"TC API launch {launch_id} did not finish in 600s")


def register(c):
    info = json.loads(protected_file(RUN / "launch.json").read_text())
    t = json.loads(run([BIN / "argus-workload", "-action", "register", "-container", info["container"]["container_ID"], "-policy", baseline(c)["policy_id"]]))
    if t["launch_id"] != info["launch_id"]:
        (RUN / "target.json").unlink()
        raise ValueError("registered launch differs from TC API response")
    return t


def start(c):
    record = preflight(c)
    render(c)
    record["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for key in ("previous_agent_unit", "previous_provider_unit"):
        if c.get(key):
            run(["systemctl", "stop", c[key]])
    # A manually started old process must be stopped by its owner. Never unlink
    # a live Provider socket or race two Agents over one data directory.
    for name in ("spire-agent", "argus-tdx-evidence-provider"):
        running = run(["pgrep", "-f", "(^|/)" + name + "( |$)"], check=False)
        if running:
            raise ValueError(f"{name} is still running with PID(s) {running}; stop the old process before start")
    run(["systemctl", "start", "argus-helper.service"])
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        s = status(c)
        if s["ready"]:
            record.update(s)
            return record
        time.sleep(1)
    run(["systemctl", "stop", "argus-helper.service"])
    raise TimeoutError("target identity did not become ready; inspect journalctl -u argus-helper -u argus-workload-agent -u argus-tdx-provider")


def status(c):
    units = {u: run(["systemctl", "is-active", u], check=False) for u in UNITS}
    try:
        serial = Path("/run/argus-credentials/ready").read_text().strip()
    except FileNotFoundError:
        serial = ""
    serial = serial if re.fullmatch(r"[0-9]+", serial) else None
    return {"units": units, "ready": all(v == "active" for v in units.values()) and serial is not None,
            "target_serial": serial}


def stop(c):
    run(["systemctl", "stop", *UNITS])
    (RUN / "target.json").unlink(missing_ok=True)
    if Path("/run/argus-credentials/ready").exists():
        raise ValueError("readiness was not removed")
    return {"stopped": True, "reregistration_required": True}


def verify(c):
    s = status(c)
    if not s["ready"]:
        raise ValueError("workload is not ready")
    target = json.loads(run([BIN / "argus-workload", "-action", "check"]))
    proof = json.loads(run([BIN / "spiffe-mtls-probe", "-url", c["business_url"], "-cert", c["client_cert"],
                           "-key", c["client_key"], "-bundle", c["client_bundle"], "-server-id", TARGET_ID]))
    if proof["client_spiffe_id"] != "spiffe://argus.local/agent/openclaw" or proof["server_serial"] != s["target_serial"]:
        raise ValueError("business call did not use the expected client/current target SVID")
    started = json.loads(protected_file(RECORDS / "start.json").read_text())["started_at"]
    journal = run(["journalctl", "-u", "argus-tdx-provider", "-u", "argus-workload-agent", "-u", "argus-helper", "--since", started, "--no-pager", "-o", "cat"])
    appraisals = [line for line in journal.splitlines() if "workload EAR accepted" in line and "launch_id=" + target["launch_id"] in line]
    if not appraisals or "target SVID published serial=" + proof["server_serial"] not in journal:
        raise ValueError("missing correlated EAR acceptance/SVID publication log for this launch")
    RECORDS.mkdir(parents=True, exist_ok=True, mode=0o700)
    (RECORDS / "last-verify-journal.log").write_text(journal)
    (RECORDS / "last-verify-journal.log").chmod(0o600)
    return {"target": target, "svid_and_business": proof, "server": remote_check(c), "appraisal": appraisals[-1],
            "evidence_kind": "COMPANY_REAL_TDX_RUN", "appraisal_log": str(RECORDS / "last-verify-journal.log")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["render", "preflight", "launch", "register", "start", "status", "stop", "verify", "server-check", "apply-entries"])
    parser.add_argument("--config", default="/etc/argus-workload/environment.json")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("run this lifecycle tool as root on the Linux company host")
    c = json.loads(protected_file(args.config).read_text())
    functions = {"render": render, "preflight": preflight, "launch": launch, "register": register, "start": start,
                 "status": status, "stop": stop, "verify": verify, "server-check": server_check,
                 "apply-entries": lambda c: server_check(c, apply=True)}
    result = functions[args.action](c)
    result["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if args.action not in ("status", "server-check"):
        write_json(RECORDS / (args.action + ".json"), result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(f"WORKLOAD_ATTESTATION=FAIL: {error}", file=sys.stderr)
        sys.exit(1)
