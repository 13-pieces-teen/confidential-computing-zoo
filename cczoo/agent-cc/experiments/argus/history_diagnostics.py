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
import time
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


def approve_reference(bundle, verifier_bin, policy_bin, output):
    """Freeze an independently collected legal run before comparison activity."""
    from admission_evidence import verify
    bundle = Path(bundle).resolve()
    result = verify(bundle, verifier_bin, policy_bin)
    require(result["result"] == "PASS" and result.get("admission", {}).get("status") == "OBSERVED_ADMITTED",
            "approved reference requires a reverified legal admission observation")
    request = read(bundle / "trustee/history-request.json")
    reference = {"schema": "argus.fixed-approved-measurement.v1", "approved_at_ms": time.time_ns() // 1000000,
                 "reference_bundle": str(bundle), "bundle_manifest_sha256": sha(bundle / "manifest.json"),
                 "rtmr2": request["rtmr2"], "target": result["target"], "nonce": result["nonce"],
                 "quote_sha256": sha(bundle / "plugin/quote.bin"),
                 "policy_sha256": sha(bundle / "approved-policy.rego"),
                 "verification_scope": result["scope"], "offline_dcap": "NOT_RUN"}
    require(not Path(output).exists(), "reference already exists; do not replace a frozen reference")
    atomic(output, reference)
    return reference


def approved_measurement(reference_file, request):
    reference = read(reference_file)
    require(reference.get("schema") == "argus.fixed-approved-measurement.v1", "unsupported approved reference")
    bundle = Path(reference["reference_bundle"])
    manifest = read(bundle / "manifest.json")
    require(sha(bundle / "manifest.json") == reference["bundle_manifest_sha256"], "reference bundle manifest changed")
    for name, expected in manifest["files"].items():
        path = (bundle / name).resolve()
        require(path.is_relative_to(bundle.resolve()) and sha(path) == expected, "reference original changed")
    original = read(bundle / "trustee/history-request.json")
    original_target = {k: v for k, v in original["runtime_data"].items() if k not in ("protocol", "nonce")}
    require(reference["rtmr2"] == original["rtmr2"] and reference["nonce"] == original["runtime_data"]["nonce"]
            and reference["target"] == original_target
            and reference["quote_sha256"] == sha(bundle / "plugin/quote.bin")
            and reference["policy_sha256"] == sha(bundle / "approved-policy.rego"), "reference association changed")
    target = {k: v for k, v in request["runtime_data"].items() if k not in ("protocol", "nonce")}
    require(target == reference["target"], "fixed measurement comparison must retain the same target and policy")
    return reference


def replay_bundle(bundle, reference_file, verifier_bin, policy_bin, output):
    from admission_evidence import verify, history_verifier
    bundle = Path(bundle).resolve()
    checked = verify(bundle, verifier_bin, policy_bin)
    require(checked["result"] == "PASS", "comparison admission originals must pass context reverification")
    request = read(bundle / "trustee/history-request.json")
    reference = approved_measurement(reference_file, request)
    require(sha(bundle / "approved-policy.rego") == reference["policy_sha256"],
            "fixed measurement comparison requires the same approved policy bytes, not only its ID")
    require(checked["nonce"] == reference["nonce"] or checked["captured_at_ms"] >= reference["approved_at_ms"],
            "reference must be frozen before comparison capture")
    result = diagnose(history_verifier(bundle), request, reference["rtmr2"])
    result.update(fixed_reference_status="FROZEN_APPROVED_CAPTURE", approved_reference_sha256=sha(reference_file),
                  bundle_manifest_sha256=checked["bundle_manifest_sha256"], same_approved_policy=True,
                  offline_dcap="NOT_RUN")
    atomic(output, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("capture")
    for name in ("config", "request", "context", "output"): a.add_argument("--" + name, required=True)
    a = sub.add_parser("replay")
    for name in ("config", "archive", "output"): a.add_argument("--" + name, required=True)
    group = a.add_mutually_exclusive_group(required=True)
    group.add_argument("--approved-reference")
    group.add_argument("--fixed-rtmr", help="legacy exploratory value; not a fixed-approved paper reference")
    a = sub.add_parser("approve-reference")
    for name in ("bundle", "verifier-bin", "policy-bin", "output"): a.add_argument("--" + name, required=True)
    a = sub.add_parser("replay-bundle")
    for name in ("bundle", "approved-reference", "verifier-bin", "policy-bin", "output"): a.add_argument("--" + name, required=True)
    args = p.parse_args()
    if args.command == "capture":
        capture(args.config, args.request, args.context, args.output)
    elif args.command == "approve-reference":
        approve_reference(args.bundle, args.verifier_bin, args.policy_bin, args.output)
    elif args.command == "replay-bundle":
        replay_bundle(args.bundle, args.approved_reference, args.verifier_bin, args.policy_bin, args.output)
    else:
        archive = read(args.archive)
        validate_archive(args.config, archive)
        reference = approved_measurement(args.approved_reference, archive["request"]) if args.approved_reference else None
        fixed = reference["rtmr2"] if reference else args.fixed_rtmr
        require(len(fixed) == 96 and all(c in "0123456789abcdef" for c in fixed), "expected 48-byte fixed RTMR")
        cls, _, _ = production()
        verifier = cls(read(args.config)); verifier.opener = ArchiveTransport(archive["entries"])
        result = diagnose(verifier, archive["request"], fixed)
        result["fixed_reference_status"] = "FROZEN_APPROVED_CAPTURE" if reference else "UNAPPROVED_EXPLORATORY"
        if reference:
            from datetime import datetime
            captured = archive["verification_context"].get("captured_at")
            require(isinstance(captured, str), "comparison capture time is missing")
            at = datetime.fromisoformat(captured.replace("Z", "+00:00"))
            require(at.tzinfo is not None, "capture time needs an explicit timezone")
            same = archive["request"]["runtime_data"]["nonce"] == reference["nonce"]
            require(same or at.timestamp() * 1000 >= reference["approved_at_ms"], "reference must be frozen before comparison capture")
            result["approved_reference_sha256"] = sha(args.approved_reference)
        result["archive_sha256"] = sha(args.archive)
        atomic(args.output, result)


if __name__ == "__main__":
    main()
