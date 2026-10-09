#!/usr/bin/env python3
import io
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
repo = next((parent for parent in ROOT.parents
             if (parent / "cczoo/agent-cc/core/tlog").is_dir()), None)
if repo is None:
    raise SystemExit("run from a checkout containing cczoo/agent-cc/core/tlog")
sys.path.insert(0, str(ROOT / "analyzer-source"))
sys.path.insert(0, str(repo / "cczoo/agent-cc/core/tlog"))
from verify_trucon import LogVerifier

class ArchiveTransport:
    def __init__(self, entries):
        self.entries = entries
    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        ref = parse_qs(url.query).get("logIndex", [url.path.rsplit("/", 1)[-1]])[0]
        if ref not in self.entries:
            raise ValueError("archive is missing a requested Rekor entry")
        stream = io.BytesIO(json.dumps({ref:self.entries[ref]}).encode())
        stream.status = 200
        return stream

failures = []
for case in sorted((ROOT / "cases").iterdir()):
    if not case.is_dir():
        continue
    request = json.loads((case / "request.json").read_text())
    expected = json.loads((case / "result.json").read_text())["expected_log_decision"]
    config = json.loads((case / "trust.offline.json").read_text())
    config["init_public_key_paths"] = [str((case / name).resolve())
                                       for name in config["init_public_key_paths"]]
    config["rekor_public_key_path"] = str((case / config["rekor_public_key_path"]).resolve())
    verifier = LogVerifier(config)
    verifier.opener = ArchiveTransport(json.loads((case / "rekor.json").read_text()))
    try:
        verifier.verify(request)
        actual = "ALLOW"
    except Exception:
        actual = "DENY"
    if actual != expected:
        failures.append(f"{case.name}: expected {expected}, got {actual}")
if failures:
    raise SystemExit("\n".join(failures))
print("PASS: seven fixed-transport production LogVerifier fixture outcomes reproduced")
