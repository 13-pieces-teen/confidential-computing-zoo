#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    failures = []
    for line in (ROOT / "SHA256SUMS").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing: {relative}")
        elif digest(path) != expected:
            failures.append(f"checksum: {relative}")

    inventory = json.loads((ROOT / "FILES.json").read_text())
    actual = sorted(
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.relative_to(ROOT) not in {
            Path("SHA256SUMS"),
            Path("FILES.json"),
        }
    )
    listed = sorted(row["path"] for row in inventory["files"])
    if actual != listed:
        failures.append("FILES.json path set differs from export")

    if failures:
        raise SystemExit("\n".join(failures))
    print(f"PASS: {len(listed)} inventoried files and all SHA-256 values verified")


if __name__ == "__main__":
    main()
