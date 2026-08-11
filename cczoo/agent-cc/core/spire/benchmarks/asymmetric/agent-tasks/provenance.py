#!/usr/bin/env python3
"""Record the exact clean Git revision used by an E8 run."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def git(repo: Path, *arguments: str, allow_failure: bool = False) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode and not allow_failure:
        message = result.stderr.strip() or "git command failed"
        raise RuntimeError(message)
    return result.stdout.strip()


def collect(repo: Path) -> dict[str, Any]:
    resolved = repo.resolve()
    commit = git(resolved, "rev-parse", "HEAD")
    branch = git(resolved, "symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True) or None
    status = git(resolved, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "schema_version": "argus-e8-source-revision-v1",
        "commit": commit,
        "branch": branch,
        "clean": not status,
        "tree_state": "clean" if not status else "dirty",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output")
    parser.add_argument("--require-clean", action="store_true")
    arguments = parser.parse_args()
    revision = collect(Path(arguments.repo))
    if arguments.require_clean and not revision["clean"]:
        print("E8 requires a clean committed worktree; commit or remove local changes first.", file=sys.stderr)
        return 2
    payload = json.dumps(revision, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        Path(arguments.output).write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
