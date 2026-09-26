#!/usr/bin/env python3
"""E1 signed fixtures for the production log sub-verifier, never hardware proof.

New ephemeral test keys sign real DSSE, owner-chain, Merkle/checkpoint and SET
material. Only Rekor transport is replaced. Outer Quote/nonce/config policy
cases are explicitly NOT_RUN here even when the log-only component accepts.
"""
import argparse
import copy
import hashlib
from pathlib import Path
import sys

from common import atomic, digest, require
from history_diagnostics import ArchiveTransport, diagnose, production


def fixture_case(directory, case):
    cls, public_key, p384 = production()
    # Reuse the same crypto fixtures already exercising production verification.
    from test_verify_trucon import fixture
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    verifier, request, entries = fixture(directory, stop=case == "hidden_stop")
    prefix = copy.deepcopy(request)
    prefix["rekor_entry_ids"] = prefix["rekor_entry_ids"][:3]
    current = bytes.fromhex("0" * 96)
    owner = p384(public_key((directory / "init.pem").read_bytes()))
    for ref in prefix["rekor_entry_ids"][1:]:
        pred, _, _ = verifier.entry(ref, owner)
        if not pred["event_type"].endswith("_build"):
            current = hashlib.sha384(current + bytes.fromhex(pred["digest"][7:])).digest()
    prefix["rtmr2"] = current.hex()
    fixed = prefix["rtmr2"]
    expected = "ALLOW"
    outer_required = []
    if case == "legal":
        request = prefix
    elif case == "unrelated_activity":
        pass
    elif case == "same_image_new_instance":
        request["runtime_data"].update(container_id="f" * 64, launch_id="new-unlogged-launch")
        expected = "DENY"
    elif case == "hidden_stop":
        # Keep the quote's post-stop RTMR while withholding the signed stop.
        request["rekor_entry_ids"] = request["rekor_entry_ids"][:-1]
        expected = "DENY"
    elif case == "config_mismatch":
        request["runtime_data"]["config_digest"] = "sha256:" + "f" * 64
        outer_required = ["fresh Quote/REPORTDATA binding", "approved configuration policy"]
    elif case == "old_evidence":
        outer_required = ["fresh challenge", "Quote/REPORTDATA matching original nonce", "current instance observation"]
    elif case == "instance_mismatch":
        request["runtime_data"]["container_id"] = "e" * 64
        expected = "DENY"
    else:
        raise ValueError("unsupported E1 case")
    # Restore production fetch as well; only its HTTP transport is archived.
    live_verifier = cls(verifier.config)
    live_verifier.opener = ArchiveTransport({ref: {ref: row} for ref, row in entries.items()})
    result = diagnose(live_verifier, request, fixed)
    result.update(case=case, expected_log_decision=expected, full_admission="NOT_RUN",
                  outer_checks_required=outer_required, request_sha256=digest(request),
                  evidence_class="synthetic signed cryptographic fixture; production log sub-verifier")
    actual = result["rules"]["full_history_current_instance"]["decision"]
    result["fixture_result"] = "PASS" if actual == expected else "FAIL"
    atomic(directory / "request.json", request)
    atomic(directory / "rekor.json", entries)
    atomic(directory / "trust.json", verifier.config)
    atomic(directory / "result.json", result)
    return result


CASES = ("legal", "unrelated_activity", "same_image_new_instance", "hidden_stop",
         "config_mismatch", "old_evidence", "instance_mismatch")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    directory = Path(args.output)
    require(not directory.exists(), "E1 fixture output already exists")
    directory.mkdir(parents=True)
    results = [fixture_case(directory / case, case) for case in CASES]
    atomic(directory / "summary.json", {"schema": "argus.e1-fixtures.v1", "results": results,
                                       "live_admission": "NOT_RUN", "test_keys": "ephemeral non-production"})
    raise SystemExit(0 if all(r["fixture_result"] == "PASS" for r in results) else 1)


if __name__ == "__main__":
    main()
