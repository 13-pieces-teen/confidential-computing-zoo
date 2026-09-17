"""One strict deployment input for the single-instance OpenViking integration."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import stat


PROFILE = "nginx-spiffe-helper-v1"
PACKAGE = Path(__file__).resolve().parents[1]
IDENTITY_KEYS = {"trust_domain", "agent_id", "helper_id", "target_id", "client_id"}
PATH_KEYS = {"install_dir", "config_dir", "records_dir", "spire_bin_dir", "run_name"}
WORKLOAD_KEYS = {"id", "config_host_path", "data_host_path", "config_path", "data_path",
                 "listen_port", "tls_port", "published_port"}
EXISTING_KEYS = {"node_agent_config", "trustee_url", "trustee_ca_path", "trustee_server_name",
                 "ear_public_key_path", "ear_expected_issuer", "ear_expected_profile", "approved",
                 "server_ssh", "server_script", "server_config", "server_unit", "server_socket",
                 "tc_api_url", "tc_api_user_id", "image_id", "image_url", "client_cert", "client_key",
                 "client_bundle", "business_url"}


def object_keys(value, required, optional=()):
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - set(optional):
        raise ValueError("missing or unknown deployment configuration fields")


def linux_path(value):
    # These paths also appear in systemd, Docker argv and NGINX. Restrict their
    # alphabet so values cannot introduce directives, expansions or mount args.
    if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or value.startswith("//") or \
            str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts:
        raise ValueError(f"expected a clean absolute Linux path: {value!r}")
    return value


def protected_file(value):
    p = Path(value)
    s = p.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise ValueError(f"{p} must be a root-owned file without group/other write")
    return p


class Deployment:
    def __init__(self, c):
        object_keys(c, EXISTING_KEYS | {"schema_version", "identity", "paths", "workload"}, {"approved_policy_artifact"})
        if type(c["schema_version"]) is not int or c["schema_version"] != 1:
            raise ValueError("unsupported deployment schema_version")
        for key in EXISTING_KEYS - {"approved"}:
            if not isinstance(c[key], str) or (not c[key] and key != "server_ssh"):
                raise ValueError(f"deployment {key} must be a string")
        object_keys(c["approved"], {"policy_id", "image_config_digest", "config_digest", "executable", "mr_td", "rtmr_0", "rtmr_1", "rtmr_2"})
        i, p, w = c["identity"], c["paths"], c["workload"]
        object_keys(i, IDENTITY_KEYS)
        object_keys(p, PATH_KEYS)
        object_keys(w, WORKLOAD_KEYS)
        if not isinstance(i["trust_domain"], str) or not re.fullmatch(r"[a-z0-9._-]+", i["trust_domain"]):
            raise ValueError("invalid trust_domain")
        prefix = "spiffe://" + i["trust_domain"]
        ids = [i[k] for k in ("agent_id", "helper_id", "target_id", "client_id")]
        for value in ids:
            if not isinstance(value, str) or not value.startswith(prefix + "/") or \
                    not re.fullmatch(r"(?:/[A-Za-z0-9_.-]+)+", value[len(prefix):]) or \
                    any(part in (".", "..") for part in value[len(prefix):].split("/")):
                raise ValueError("SPIFFE IDs must be unescaped identities in the configured trust domain")
        if len(set(ids)) != len(ids):
            raise ValueError("Agent, Helper, target and client identities must be distinct")
        node = i["agent_id"].removeprefix(prefix + "/spire/agent/argus_tdx/")
        if not i["agent_id"].startswith(prefix + "/spire/agent/argus_tdx/") or \
                not re.fullmatch(r"[A-Za-z0-9_.-]+", node) or not node.strip("."):
            raise ValueError("invalid argus_tdx Agent identity")
        if not isinstance(w["id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", w["id"]):
            raise ValueError("invalid workload id")
        for key in ("listen_port", "tls_port", "published_port"):
            if type(w[key]) is not int or not 1 <= w[key] <= 65535:
                raise ValueError(f"invalid workload {key}")
        if w["listen_port"] == w["tls_port"]:
            raise ValueError("business and TLS listener ports must differ")
        for key in WORKLOAD_KEYS - {"id", "listen_port", "tls_port", "published_port"}:
            linux_path(w[key])
        if PurePosixPath(w["config_path"]).is_relative_to(w["data_path"]) or \
                PurePosixPath(w["config_host_path"]).is_relative_to(w["data_host_path"]):
            raise ValueError("configuration must not be inside the writable data directory")
        executable = c["approved"]["executable"]
        linux_path(executable)
        if any(PurePosixPath(value).is_relative_to("/tmp") for value in (w["config_path"], w["data_path"], executable)) or PurePosixPath(executable).is_relative_to(w["data_path"]):
            raise ValueError("application configuration and executable must remain outside writable mounts")
        for key in PATH_KEYS - {"run_name"}:
            linux_path(p[key])
            if p[key] in ("/etc", "/opt", "/var", "/run", "/usr"):
                raise ValueError("deployment directories must have a dedicated child directory")
        roots = [PurePosixPath(p[k]) for k in PATH_KEYS - {"run_name"}]
        if any(a.is_relative_to(b) for a in roots for b in roots if a != b) or len(set(roots)) != len(roots):
            raise ValueError("deployment directories must not overlap")
        if not isinstance(p["run_name"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", p["run_name"]):
            raise ValueError("run_name must be a short lowercase systemd directory prefix")
        runtime_roots = [PurePosixPath("/run") / (p["run_name"] + suffix) for suffix in
                         ("", "-workload", "-credentials", "-nginx", "-authz", "-spire-agent", "-spire-broker")]
        if any(a.is_relative_to(b) or b.is_relative_to(a) for a in roots for b in runtime_roots):
            raise ValueError("deployment directories must not overlap runtime cleanup directories")
        for key in ("node_agent_config", "trustee_ca_path", "ear_public_key_path", "server_script", "server_config",
                    "server_socket", "client_cert", "client_key", "client_bundle"):
            linux_path(c[key])
        self.c, self.identity, self.workload = c, i, w
        self.install = Path(p["install_dir"])
        self.bin = self.install / "bin"
        self.etc = Path(p["config_dir"])
        self.records = Path(p["records_dir"])
        self.spire = Path(p["spire_bin_dir"])
        self.run_name = p["run_name"]
        self.run = Path("/run") / (self.run_name + "-workload")
        self.credentials = Path("/run") / (self.run_name + "-credentials")
        self.nginx = Path("/run") / (self.run_name + "-nginx")
        self.authz = Path("/run") / (self.run_name + "-authz")
        self.agent = Path("/run") / (self.run_name + "-spire-agent")
        self.broker = Path("/run") / (self.run_name + "-spire-broker")
        self.provider_socket = Path("/run") / self.run_name / "evidence-provider.sock"
        self.target = self.run / "target.json"
        self.environment = self.etc / "environment.json"

    def policy_values(self):
        return {**self.c["approved"], "agent_id": self.identity["agent_id"], "workload_id": self.workload["id"],
                "config_path": self.workload["config_path"], "listen_port": str(self.workload["listen_port"])}

    def tokens(self):
        return {"BIN": self.bin.as_posix(), "INSTALL": self.install.as_posix(), "ETC": self.etc.as_posix(),
                "SPIRE": self.spire.as_posix(), "RUN_NAME": self.run_name, "TARGET": self.target.as_posix(),
                "CREDENTIALS": self.credentials.as_posix(), "NGINX_RUN": self.nginx.as_posix(),
                "AUTHZ_SOCKET": (self.authz / "authz.sock").as_posix(),
                "PROVIDER_SOCKET": self.provider_socket.as_posix(), "AGENT_SOCKET": (self.agent / "agent.sock").as_posix(),
                "BROKER_SOCKET": (self.broker / "broker.sock").as_posix(),
                "AGENT_ID": self.identity["agent_id"], "HELPER_ID": self.identity["helper_id"],
                "TARGET_ID": self.identity["target_id"], "CLIENT_ID": self.identity["client_id"],
                "WORKLOAD_ID": self.workload["id"], "DATA_PATH": self.workload["data_path"],
                "TLS_PORT": str(self.workload["tls_port"]), "LISTEN_PORT": str(self.workload["listen_port"])}

    def render(self, text):
        tokens = self.tokens()
        return re.sub(r"@([A-Z_]+)@", lambda m: tokens[m[1]], text)


def write_file(path, contents, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".next")
    # Never follow a leftover temporary symlink.
    import os
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(contents)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render_services(c, package=PACKAGE):
    d = Deployment(c)
    d.etc.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("helper.conf", "nginx.conf"):
        write_file(d.etc / name, d.render((package / "config" / name).read_text()))
    for path in (package / "systemd").glob("*.service"):
        write_file(d.etc / "systemd" / path.name, d.render(path.read_text()), 0o644)
    write_file(d.bin / "nginx-hook.sh", d.render((package / "scripts/nginx-hook.sh").read_text()), 0o755)
    write_file(d.environment, json.dumps(c, indent=2) + "\n")
    # Only TC API launch settings are exported, never Trustee or client keys.
    write_file(d.etc / "tc-api-workload.json", json.dumps({"schema_version": 1, "workload": d.workload}, indent=2) + "\n")
    return d


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("paths", "render-services"))
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    c = json.loads(protected_file(args.config).read_text())
    d = Deployment(c)
    if args.action == "paths":
        for value in (d.install, d.etc, d.records, d.spire, d.run):
            print(value.as_posix())
    else:
        render_services(c)


if __name__ == "__main__":
    main()
