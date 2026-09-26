#!/usr/bin/env python3
"""Build an explicit audit image and record reproducible source/image metadata."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", required=True)
    p.add_argument("--manifest", required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parent
    manifest = Path(args.manifest)
    if manifest.exists():
        raise ValueError("build manifest already exists")
    lock = json.loads((root / "upstream-lock.json").read_text())
    files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "-f", str(root / "Dockerfile"),
                    "--tag", args.tag, str(root.parent)], check=True)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", args.tag], text=True))[0]
    result = {"schema_version": 1, "base_image": lock["base_image"], "upstream_revision": lock["source_revision"],
              "image_id": image["Id"], "repo_digests": image.get("RepoDigests", []), "files": files,
              "remote_acceptance": "NOT_RUN", "default_mode": "on", "boundary": "asgi_application_read"}
    with manifest.open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print(json.dumps({"image_id": image["Id"], "manifest": str(manifest)}))


if __name__ == "__main__":
    main()
