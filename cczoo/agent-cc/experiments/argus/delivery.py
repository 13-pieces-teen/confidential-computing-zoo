#!/usr/bin/env python3
"""Produce/verify a portable source manifest, separate from Linux binary builds."""
import argparse
import hashlib
from pathlib import Path
import subprocess

from common import atomic, read, require

ROOT = Path(__file__).resolve().parents[2]
SCOPES = ["ARGUS.md", "README.md", "README_CN.md", "experiments/README.md",
          "experiments/ARGUS-SCOPE-REDUCTION-PLAN-20260926.md",
          "adapters/OpenClaw", "adapters/OpenViking", "experiments/argus", "core/spire/workload",
          "core/spire/helpers/spiffe-helper", "core/tc_api/tc_api/services/launch.py",
          "core/tc_api/tc_api/services/workload_profile.py", "core/tc_api/tests/test_workload_profile.py"]
EXCLUDED = {"__pycache__", ".pytest_cache", "node_modules", "dist", "build", ".git", ".venv", "venv", "vendor"}
SUFFIXES = {".py", ".go", ".mod", ".sum", ".json", ".md", ".mjs", ".js", ".ts", ".sh", ".service", ".hcl", ".conf", ".rego", ".txt", ".yaml", ".yml", ".lock", ".rs", ".toml", ".proto"}
LOCAL_OUTPUTS = ("experiments/argus/private/", "experiments/argus/evidence/", "experiments/argus/generated/")


def files(exclude, *, normalize_newlines=False):
    result = {}
    for scope in SCOPES:
        source = ROOT / scope
        for path in ([source] if source.is_file() else source.rglob("*")):
            if (not path.is_file() or path.resolve() == exclude or path.name == "source-manifest.json"
                    or path.relative_to(ROOT).as_posix().startswith(LOCAL_OUTPUTS)
                    or any(p in EXCLUDED for p in path.relative_to(ROOT).parts)):
                continue
            if path.suffix in SUFFIXES or path.name in ("Dockerfile", ".gitignore", ".dockerignore", ".gitattributes"):
                content = path.read_bytes()
                if normalize_newlines:
                    content = content.replace(b"\r\n", b"\n")
                result[path.relative_to(ROOT).as_posix()] = hashlib.sha256(content).hexdigest()
    return dict(sorted(result.items()))


def generate(output):
    output = Path(output).resolve()
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    patch = subprocess.check_output(["git", "-C", str(ROOT), "diff", "HEAD", "--binary", "--", *SCOPES,
                                     ":(exclude)**/source-manifest.json"])
    value = {"schema": "argus.source-delivery.v1", "baseline_commit": commit,
             "tracked_scope_patch_sha256": hashlib.sha256(patch).hexdigest(),
             "source_sha256_normalization": "CRLF to LF only; no other whitespace changes",
             "files": files(output, normalize_newlines=True), "workspace_files_sha256": files(output),
             "binary_manifest": "produce current core/spire/workload build-manifest.json using scripts/build.sh",
             "audit_image_manifest": "produce using adapters/OpenViking/receiver_audit/build.py",
             "real_tdx": "NOT_RUN", "remote_acceptance": "NOT_RUN"}
    atomic(output, value)
    return value


def verify(path):
    expected = read(path)
    require(expected.get("schema") == "argus.source-delivery.v1", "unsupported source manifest")
    normalized = expected.get("source_sha256_normalization") == "CRLF to LF only; no other whitespace changes"
    actual = files(Path(path).resolve(), normalize_newlines=normalized)
    require(actual == expected["files"], "source manifest differs: regenerate only after reviewing changes")
    return {"result": "PASS", "files": len(actual), "scope": "source content with declared newline normalization",
            "original_workspace_bytes_match": files(Path(path).resolve()) == expected.get("workspace_files_sha256", expected["files"])}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("create", "verify")); p.add_argument("--manifest", required=True)
    a = p.parse_args()
    result = generate(a.manifest) if a.action == "create" else verify(a.manifest)
    print(__import__("json").dumps({"files": len(result["files"]), "result": "CREATED"} if a.action == "create" else result))


if __name__ == "__main__": main()
