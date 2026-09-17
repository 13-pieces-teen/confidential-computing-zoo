#!/usr/bin/env python3
"""NGINX publication and cleanup using the same deployment as Helper."""
import argparse
import json
import os
import shutil
from deployment import Deployment, protected_file
from workload import run


def hook(c, action):
    d = Deployment(c)
    nginx = ["nginx", "-c", d.etc / "nginx.conf"]
    if action in ("publish", "exec"):
        target = json.loads(run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
        namespace = ["nsenter", "--target", target["pid"], "--net"]
    if action == "publish":
        d.nginx.mkdir(parents=True, exist_ok=True, mode=0o755)
        run([*nginx, "-t"])
        active = run(["systemctl", "is-active", "argus-nginx.service"], check=False) == "active"
        run(["systemctl", "reload" if active else "start", "argus-nginx.service"])
        run(["systemctl", "is-active", "--quiet", "argus-nginx.service"])
        run([*namespace, d.bin / "spiffe-mtls-probe", "-tls-only", "-url", f"https://127.0.0.1:{d.workload['tls_port']}",
             "-server-id", d.identity["target_id"], "-cert", d.credentials / "current/svid.pem",
             "-key", d.credentials / "current/key.pem", "-bundle", d.credentials / "current/bundle.pem"])
    elif action == "reload":
        run([*nginx, "-t"])
        run([*nginx, "-s", "reload"])
    elif action == "exec":
        argv = [str(v) for v in [*namespace, *nginx, "-g", "daemon off;"]]
        os.execvp(argv[0], argv)
    elif action == "stop":
        run(["systemctl", "stop", "argus-nginx.service"])
    elif action == "clear":
        if d.credentials.is_symlink():
            raise ValueError("credential directory must not be a symlink")
        if d.credentials.exists():
            for child in d.credentials.iterdir():
                if child.is_symlink() or child.is_file():
                    child.unlink()
                elif child.name.startswith("generation-"):
                    shutil.rmtree(child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("publish", "reload", "exec", "stop", "clear"))
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    hook(json.loads(protected_file(args.config).read_text()), args.action)


if __name__ == "__main__":
    main()
