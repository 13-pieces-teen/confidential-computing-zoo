#!/usr/bin/env python3
"""Apply the Argus transport overlay to a checksum-pinned upstream release."""
import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent
LOCK = json.loads((ROOT / "upstream.lock.json").read_text())


def build(archive: bytes) -> tuple[bytes, dict]:
    expected = base64.b64decode(LOCK["integrity"].removeprefix("sha512-"))
    if hashlib.sha512(archive).digest() != expected:
        raise ValueError("upstream SHA-512 integrity mismatch")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.parts[0] != "package" or path.is_absolute() or ".." in path.parts:
                raise ValueError("unsafe upstream archive path")
            if member.isdir():
                continue
            if not member.isfile() or member.size > 4 * 1024 * 1024 or member.name in files:
                raise ValueError("unsafe upstream archive member")
            files[member.name] = source.extractfile(member).read()
    if sum(map(len, files.values())) > 16 * 1024 * 1024:
        raise ValueError("upstream archive too large")
    package = json.loads(files["package/package.json"])
    if package["name"] != LOCK["name"] or package["version"] != LOCK["version"]:
        raise ValueError("upstream package identity mismatch")

    patches = {}

    def replace(path, before, after, count):
        text = files[path].decode()
        if text.count(before) != count:
            raise ValueError(f"unexpected upstream source at {path}")
        patches[path] = hashlib.sha256(files[path]).hexdigest()
        files[path] = text.replace(before, after).encode()

    # Patch the published JavaScript AND its source. CLI setup has three direct
    # fetch calls independent of the normal client's HttpTransport adapter.
    for extension, prefix in (("ts", "package/"), ("js", "package/dist/")):
        adapter = prefix + "adapters/http-transport." + extension
        replace(adapter, "(url, init) => fetch(url, init)", "(url, init) => spiffeFetch(url, init)", 1)
        files[adapter] = b'import { spiffeFetch } from "../argus-spiffe/transport.mjs";\n' + files[adapter]
        setup = prefix + "commands/setup." + extension
        replace(setup, "await fetch(", "await spiffeFetch(", 3)
        files[setup] = b'import { spiffeFetch } from "../argus-spiffe/transport.mjs";\n' + files[setup]
        engine = prefix + "context-engine." + extension
        replace(engine, "return assembleOpenVikingSession({", "return auditAssembly(assembleOpenVikingSession, {", 1)
        files[engine] = b'import { auditAssembly } from "./argus-spiffe/recall-audit.mjs";\n' + files[engine]
        lifecycle = prefix + "services/context-lifecycle-service." + extension
        replace(lifecycle, "return { messages: withRecall, estimatedTokens };",
                "auditRecallSource(recall.block);\n            return { messages: withRecall, estimatedTokens };", 1)
        files[lifecycle] = b'import { auditRecallSource } from "../argus-spiffe/recall-audit.mjs";\n' + files[lifecycle]
    probe = "package/services/setup/probe-service.ts"
    replace(probe, "(url, init) => fetch(url, init)", "(url, init) => spiffeFetch(url, init)", 1)
    files[probe] = b'import { spiffeFetch } from "../../argus-spiffe/transport.mjs";\n' + files[probe]
    for module in (ROOT / "lib").glob("*.mjs"):
        for prefix in ("package/argus-spiffe/", "package/dist/argus-spiffe/"):
            files[prefix + module.name] = module.read_bytes()

    # Keep upstream's version for its strict feature-gate version parser. The
    # separate marker and whole-package digest distinguish the customized build.
    package["argusSpiffe"] = {"revision": LOCK["customization"], "upstreamIntegrity": LOCK["integrity"]}
    package["engines"]["node"] = ">=22.17.0"
    package["dependencies"] = LOCK["runtimeDependencies"]
    package["files"].append("argus-spiffe/")
    package["scripts"] = {}  # The published JS is patched directly; no absent upstream build inputs.
    package.pop("devDependencies", None)
    manifest = json.loads(files["package/install-manifest.json"])
    manifest["npm"]["build"] = False
    files["package/install-manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    files["package/package.json"] = (json.dumps(package, ensure_ascii=False, indent=2) + "\n").encode()
    files["package/ARGUS-UPSTREAM.json"] = (json.dumps({**LOCK, "patchedSourceSHA256": patches}, indent=2) + "\n").encode()
    package_bytes = io.BytesIO()
    with gzip.GzipFile(fileobj=package_bytes, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target:
            for name, data in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.mtime = len(data), 0o644, 0
                target.addfile(member, io.BytesIO(data))
    result = package_bytes.getvalue()
    return result, {"name": package["name"], "version": package["version"], "customization": LOCK["customization"],
                    "sha256": hashlib.sha256(result).hexdigest(), "upstreamIntegrity": LOCK["integrity"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, help="offline upstream tgz, still integrity checked")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "openviking-openclaw-plugin-2026.6.18-argus.2.tgz")
    args = parser.parse_args()
    if args.upstream:
        archive = args.upstream.read_bytes()
    else:
        with urllib.request.urlopen(LOCK["url"], timeout=30) as response:
            archive = response.read(8 * 1024 * 1024)
    result, receipt = build(archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)
    args.output.with_suffix(".json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({**receipt, "artifact": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
