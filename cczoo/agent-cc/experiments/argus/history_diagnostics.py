#!/usr/bin/env python3
"""Offline E1 rules using production cryptography and production history appraisal.

Only the Rekor transport is replaced with archived responses. This is an offline
log-subverifier diagnostic, NOT a fresh TDX admission decision. Quote, REPORTDATA,
challenge and policy evidence stay separate in the archived verification context.
"""
import argparse
import copy
import io
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit, parse_qs

from common import atomic, digest, read, require, sha


def production():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "core" / "tlog"))
    sys.path.insert(0, str(root / "core" / "spire" / "workload" / "trustee"))
    from verify_trucon import LogVerifier, public_key, p384
    return LogVerifier, public_key, p384


def trust_artifacts(config):
    """The JSON path alone does not pin the bytes of trust material it names."""
    paths = [config["rekor_public_key_path"]] + config.get("init_public_key_paths", [])
    if config.get("sigstore_trusted_root_path"):
        paths.append(config["sigstore_trusted_root_path"])
    require(all(isinstance(name, str) and Path(name).is_absolute() for name in paths),
            "offline capture requires absolute trust material paths")
    return {str(Path(name).resolve()): sha(name) for name in paths}


def validate_archive(config_file, archive):
    require(archive.get("schema") == "argus.history-archive.v1", "unsupported history archive")
    require(archive["config_sha256"] == sha(config_file), "trust configuration differs from capture")
    require(archive.get("trust_artifacts") == trust_artifacts(read(config_file)), "captured trust material changed")
    require(archive.get("request_sha256") == digest(archive["request"]), "captured verifier request changed")
    require(archive.get("context_sha256") == digest(archive["verification_context"]), "captured verification context changed")
    context = archive["verification_context"]
    require(context.get("request_sha256") == digest(archive["request"]), "context is not associated with this verifier request")
    require(context.get("nonce") == archive["request"]["runtime_data"].get("nonce"), "captured challenge mismatch")
    require(archive.get("context_artifacts"), "captured context artifact coverage missing")
    require(sorted(archive["context_artifacts"].values()) == sorted(context.get("verification_artifacts", {}).values()),
            "captured context artifact set differs from original context")
    for path, expected in archive["context_artifacts"].items():
        require(sha(path) == expected, "captured verification context artifact changed")


class ArchiveTransport:
    """Preserves fetch's reference/index/body checks; never substitutes a verdict."""
    def __init__(self, entries):
        self.entries = entries

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        ref = parse_qs(url.query).get("logIndex", [url.path.rsplit("/", 1)[-1]])[0]
        require(ref in self.entries, "archive is missing a requested Rekor entry")
        stream = io.BytesIO(json.dumps(self.entries[ref]).encode())
        stream.status = 200
        return stream


def diagnose(verifier, request, fixed_rtmr):
    _, public_key, p384 = production()
    results = {
        "fixed_accumulated_measurement": {
            "decision": "ALLOW" if request["rtmr2"] == fixed_rtmr else "DENY",
            "missing": ["event meaning", "current instance binding", "tolerance of unrelated measured activity"]},
        "target_launch_only": {"decision": "UNKNOWN", "missing": ["history completeness", "subsequent stop/removal", "full RTMR reproduction"]},
        "full_history_current_instance": {"decision": "UNKNOWN", "implementation": "production LogVerifier.verify",
            "not_checked_here": ["TDX Quote", "REPORTDATA", "challenge freshness", "current PID/starttime", "configuration admission policy"]}}
    # Authenticate original DSSE/Rekor statements and initialized owner, but
    # deliberately omit chain/RTMR/later-event semantics for the weak rule.
    try:
        refs, runtime = request["rekor_entry_ids"], request["runtime_data"]
        first, _, _ = verifier.entry(refs[0], None)
        require(first["event_type"] == "chain.init", "missing initialization")
        values = {e["key"]: e["value"] for e in first["entries"]}
        owner = p384(public_key(values["pub_key"].encode()))
        found = False
        for ref in refs[1:]:
            pred, _, _ = verifier.entry(ref, owner)
            values = {e["key"]: e["value"] for e in pred["entries"]}
            info = values.get("container_info", {})
            if (values.get("launch_result") == "success" and info.get("container_Status") == "running"
                and all(info.get(a) == runtime.get(b) for a, b in (
                    ("workload_id", "workload_id"), ("launch_id", "launch_id"),
                    ("container_ID", "container_id"), ("runtime_image_config_digest", "image_config_digest")))):
                found = True
        results["target_launch_only"]["decision"] = "ALLOW" if found else "DENY"
    except Exception as exc:
        results["target_launch_only"].update(decision="DENY", error_class=type(exc).__name__)
    try:
        verdict = verifier.verify(copy.deepcopy(request))
        results["full_history_current_instance"].update(decision="ALLOW", verified=verdict)
    except Exception as exc:
        results["full_history_current_instance"].update(decision="DENY", reason=str(exc))
    return {"schema": "argus.history-diagnostics.v1", "rules": results,
            "evidence_class": "offline production log-verifier replay", "live_admission": "NOT_RUN",
            "quote_and_freshness": "not re-evaluated by this log-only tool",
            "request_sha256": digest(request)}


def capture(config, request_file, context_file, output):
    cls, _, _ = production()
    request, context = read(request_file), read(context_file)
    require(context.get("captured_at") and context.get("nonce") == request["runtime_data"].get("nonce"), "capture requires matching original challenge context")
    require(context.get("request_sha256") == digest(request), "capture context must name the canonical verifier request SHA256")
    require(context.get("verification_artifacts"), "archive original Quote/REPORTDATA/policy result artifacts")
    base = Path(context_file).resolve().parent
    artifacts = {}
    for filename, expected in context["verification_artifacts"].items():
        path = (base / filename).resolve()
        require(sha(path) == expected, "verification context artifact mismatch")
        artifacts[str(path)] = expected
    trust = trust_artifacts(read(config))
    verifier = cls(read(config))
    entries = {}
    for ref in request["rekor_entry_ids"]:
        uuid, raw = verifier.fetch(ref)
        entries[ref] = {uuid: raw}
    archive = {"schema": "argus.history-archive.v1", "request": request, "entries": entries,
               "verification_context": context, "context_artifacts": artifacts,
               "trust_artifacts": trust, "request_sha256": digest(request), "context_sha256": digest(context),
               "config_sha256": sha(config), "evidence_class": "captured-context, not live admission"}
    atomic(output, archive)
    return archive


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("capture")
    for name in ("config", "request", "context", "output"): a.add_argument("--" + name, required=True)
    a = sub.add_parser("replay")
    for name in ("config", "archive", "fixed-rtmr", "output"): a.add_argument("--" + name, required=True)
    args = p.parse_args()
    if args.command == "capture":
        capture(args.config, args.request, args.context, args.output)
    else:
        archive = read(args.archive)
        validate_archive(args.config, archive)
        require(len(args.fixed_rtmr) == 96 and all(c in "0123456789abcdef" for c in args.fixed_rtmr), "expected 48-byte fixed RTMR")
        cls, _, _ = production()
        verifier = cls(read(args.config)); verifier.opener = ArchiveTransport(archive["entries"])
        result = diagnose(verifier, archive["request"], args.fixed_rtmr)
        result["archive_sha256"] = sha(args.archive)
        atomic(args.output, result)


if __name__ == "__main__":
    main()
