#!/usr/bin/env python3
"""Independent re-verification of the seven E1 offline history fixtures.

Differs from the archived verify_e1_fixtures.py in one essential way:
  verify_e1_fixtures.py maps ANY Exception to DENY.
This script classifies each non-ALLOW outcome as
  RULE_DENY   - a verifier rule violation raised from LogVerifier._verify /
                entry / require() checks (the fixture's expected rejection)
  ERROR       - transport/archive gap, missing dependency, missing key file,
                config/path failure, JSON/format errors, deadline exceeded
ERROR/UNKNOWN is never counted as a successful rejection.

Nothing is modified; all inputs are read from the results branch and the
deployment checkout (tlog tree sha 63d41663... identical at fixture commit
3cf25a87 and deployment HEAD 12b365ed).

Outputs: seven-cases-decisions.csv, seven-cases-reasons.json
"""
import hashlib
import io
import json
import sys
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FIX = Path("/root/argus-results-ip2-20261009-worktree/cczoo/agent-cc/"
           "documents_ly/paper-results-20261009/ip2/batches/"
           "20261009-repair-and-rerun/preexisting/e1-offline-history-fixtures")
DEPLOY = Path("/home/ying_liu/confidential-computing-zoo")
TLOG = DEPLOY / "cczoo/agent-cc/core/tlog"
OUT = Path(__file__).resolve().parent

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

sys.path.insert(0, str(FIX / "analyzer-source"))
sys.path.insert(0, str(TLOG))
from verify_trucon import LogVerifier  # noqa: E402

RUNTIME_ERROR_MARKERS = (
    "archive is missing a requested Rekor entry",
    "Rekor verification deadline exceeded",
)

class ArchiveTransport:
    def __init__(self, entries):
        self.entries = entries
    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        ref = parse_qs(url.query).get("logIndex", [url.path.rsplit("/", 1)[-1]])[0]
        if ref not in self.entries:
            raise ValueError("archive is missing a requested Rekor entry")
        stream = io.BytesIO(json.dumps({ref: self.entries[ref]}).encode())
        stream.status = 200
        return stream

def classify(exc):
    """Map an exception to (classification, reason)."""
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "ERROR", f"missing dependency: {exc}"
    if isinstance(exc, (FileNotFoundError, PermissionError, OSError)):
        return "ERROR", f"key material / file access: {exc}"
    if isinstance(exc, ValueError):
        msg = str(exc)
        if any(m in msg for m in RUNTIME_ERROR_MARKERS):
            return "ERROR", msg
        # Everything else from require() inside the verifier is a rule check.
        return "RULE_DENY", msg
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, AttributeError)):
        return "ERROR", f"{type(exc).__name__}: {exc}"
    return "ERROR", f"unexpected {type(exc).__name__}: {exc}"

results = []
for case in sorted((FIX / "cases").iterdir()):
    if not case.is_dir():
        continue
    rec = {"case": case.name}
    try:
        request = json.loads((case / "request.json").read_text())
        result_hist = json.loads((case / "result.json").read_text())
        expected = result_hist["expected_log_decision"]
        rec["expected"] = expected
        rec["request_sha256"] = sha(case / "request.json")
        rec["expected_request_sha256"] = result_hist.get("request_sha256")
        rec["input_material_sha256"] = {
            "request.json": sha(case / "request.json"),
            "rekor.json": sha(case / "rekor.json"),
            "trust.offline.json": sha(case / "trust.offline.json"),
            "trust.original.json": sha(case / "trust.original.json"),
            "init.pem": sha(case / "init.pem"),
            "rekor.pem": sha(case / "rekor.pem"),
        }
        config = json.loads((case / "trust.offline.json").read_text())
        config["init_public_key_paths"] = [str((case / n).resolve())
                                           for n in config["init_public_key_paths"]]
        config["rekor_public_key_path"] = str((case / config["rekor_public_key_path"]).resolve())
        try:
            verifier = LogVerifier(config)
        except (FileNotFoundError, PermissionError, OSError) as e:
            rec.update(classification="ERROR", actual=None,
                       reason=f"verifier construction: {e}")
            results.append(rec)
            continue
        except ValueError as e:
            rec.update(classification="ERROR", actual=None,
                       reason=f"config rejected: {e}")
            results.append(rec)
            continue
        verifier.opener = ArchiveTransport(json.loads((case / "rekor.json").read_text()))
        try:
            verifier.verify(request)
            actual, classification, reason = "ALLOW", "ALLOW", None
        except Exception as e:
            classification, reason = classify(e)
            actual = "DENY" if classification == "RULE_DENY" else None
        timings = {k: verifier.timings.get(k) for k in
                   ("outcome", "verify_ms", "fetch_ms", "fetched_entries",
                    "fetched_bytes", "requested_entries")}
        rec.update(classification=classification, actual=actual, reason=reason,
                   timings=timings,
                   historical_rules=result_hist.get("rules"))
    except Exception as e:
        rec.update(classification="ERROR", actual=None,
                   reason=f"case loading failed ({type(e).__name__}): {e}")
    results.append(rec)

def verdict(r):
    exp, act, cls = r.get("expected"), r.get("actual"), r.get("classification")
    if cls == "ERROR":
        return "ERROR"
    if exp == act:
        return "PASS"
    return "MISMATCH"

# loaded validator digests
import tlog, tlog.digest, tlog.backends.rekor.adapter, verify_trucon  # noqa: E401
loaded = {}
import importlib
for name in ("verify_trucon", "tlog.digest", "tlog.backends.rekor.adapter",
             "tlog", "tlog.backends.rekor"):
    mod = sys.modules.get(name)
    if mod and getattr(mod, "__file__", None):
        p = Path(mod.__file__).resolve()
        if p.exists() and p.is_file():
            loaded[name] = {"file": str(p), "sha256": sha(p)}

meta = {
    "schema": "argus.paper-final.e1-seven-cases.v1",
    "python": sys.version,
    "executable": sys.executable,
    "fixture_dir": str(FIX),
    "tlog_tree_sha256": "63d416636f00ca195f193f983dcabfcd5a7612c5",
    "tlog_identical_at": {"3cf25a87": True, "deployment_head_12b365ed": True},
    "deployment_head": "12b365ed8d3c73965e69c8fbe3fcb46075fefa6c",
    "sigstore_version": __import__("sigstore").__version__,
    "cryptography_version": __import__("cryptography").__version__,
    "rekor_types_file": __import__("rekor_types").__file__,
    "analyzer_source_sha256": {
        "admission_cases.py": sha(FIX / "analyzer-source/admission_cases.py"),
        "history_diagnostics.py": sha(FIX / "analyzer-source/history_diagnostics.py"),
        "verify_trucon.py": sha(FIX / "analyzer-source/verify_trucon.py"),
    },
    "versions_json_expected": json.loads((FIX / "VERSIONS.json").read_text())["files"],
    "loaded_validator_digests": loaded,
    "results": results,
    "verdicts": {r["case"]: verdict(r) for r in results},
    "note": ("RULE_DENY = verifier rule violation (expected rejection); "
             "ERROR = transport/archive gap, missing deps/files, config or "
             "format failure - never counted as a successful rejection; "
             "Fixed/Launch/History are rule-level comparisons, not a full "
             "Native SPIRE online comparison"),
}

import csv
with open(OUT / "seven-cases-decisions.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["case", "expected", "actual",
                                      "classification", "verdict", "reason",
                                      "outcome_timing", "verify_ms",
                                      "fetched_entries", "fetched_bytes"])
    w.writeheader()
    for r in results:
        t = r.get("timings") or {}
        w.writerow({
            "case": r["case"], "expected": r.get("expected"),
            "actual": r.get("actual"), "classification": r.get("classification"),
            "verdict": verdict(r),
            "reason": (r.get("reason") or "")[:400],
            "outcome_timing": t.get("outcome"), "verify_ms": t.get("verify_ms"),
            "fetched_entries": t.get("fetched_entries"),
            "fetched_bytes": t.get("fetched_bytes")})

(OUT / "seven-cases-reasons.json").write_text(json.dumps(meta, indent=1) + "\n")
print(json.dumps({r["case"]: {"expected": r.get("expected"),
                              "actual": r.get("actual"),
                              "classification": r.get("classification"),
                              "verdict": verdict(r),
                              "reason": (r.get("reason") or "")[:200]}
                  for r in results}, indent=1))
print("wrote seven-cases-decisions.csv + seven-cases-reasons.json")
