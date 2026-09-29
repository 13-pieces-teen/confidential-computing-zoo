"""Association tests; EAR cryptography is covered by the production Go tests.

These tests do not claim TDX execution. The CLI stub only avoids compiling tools
inside a Python unit test; signed Rekor replay still uses the production verifier.
"""
import base64
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import admission_evidence as evidence
from common import atomic, sha
from history_diagnostics import production, approved_measurement, approve_reference, replay_bundle
production()
from test_verify_trucon import fixture
from verify_trucon import export_capture


def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def bundle_fixture(tmp_path, monkeypatch):
    verifier, request, raw = fixture(tmp_path)
    verifier.capture_entries = {k: {k: v} for k, v in raw.items()}
    history_config = tmp_path / "history-config.json"
    atomic(history_config, verifier.config)
    export_capture(str(tmp_path / "trustee"), request, history_config, verifier, verifier.verify(request))
    nonce = request["runtime_data"]["nonce"]
    trustee = tmp_path / "trustee" / nonce
    claims = {"hardware": 2, "executables": 3, "configuration": 2}
    atomic(trustee / "policy-input.json", {"runtime_data_claims": request["runtime_data"],
           "tdx": {"trucon": verifier.verify(request), "quote": {"body": {"rtmr_2": request["rtmr2"]}}}})
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    quote = b"fixture bytes, no DCAP claim"
    artifact = {"evidence_type": "tdx_quote", "quote": base64.urlsafe_b64encode(quote).decode().rstrip("="),
                "runtime_data": request["runtime_data"], "rekor_entry_ids": request["rekor_entry_ids"]}
    atomic(plugin / "evidence.json", artifact)
    inner = {"quote": base64.b64encode(quote).decode(), "rekor_entry_ids": request["rekor_entry_ids"]}
    atomic(plugin / "request.json", {"policy_ids": [request["runtime_data"]["policy_id"]], "verification_requests": [{
        "tee": "tdx", "runtime_data_hash_algorithm": "sha384", "evidence": encoded(inner),
        "runtime_data": {"structured": request["runtime_data"]}}]})
    (plugin / "quote.bin").write_bytes(quote)
    (plugin / "ear.jwt").write_text("header." + encoded({"submods": {"cpu0": {"ear.trustworthiness-vector": claims}}}) + ".signature")
    atomic(plugin / "capture.json", {"schema": "argus.admission-export.v1", "nonce": nonce, "captured_at_ms": 1700000000000,
           "artifacts": {p.name: sha(p) for p in plugin.iterdir()}})
    target = {k: v for k, v in request["runtime_data"].items() if k not in ("nonce", "protocol")}
    atomic(plugin / "local-check.json", {"nonce": nonce, "target": target, "matched": True})
    policy = tmp_path / "policy.rego"
    policy.write_text("approved fixture policy")
    config = tmp_path / "verification.json"
    atomic(config, {"ear_public_key_path": verifier.config["rekor_public_key_path"], "ear_expected_issuer": "issuer",
           "ear_expected_profile": "profile", "policy_id": request["runtime_data"]["policy_id"],
           "approved_policy_artifact": {"path": str(policy), "sha256": sha(policy)}, "history_config_sha256": sha(history_config)})
    output = tmp_path / "bundle"
    evidence.collect(plugin, trustee, config, output)
    monkeypatch.setattr(evidence, "_run", lambda args: {"result": "PASS"} if str(args[0]) == "ear" else {"trust_claims": claims})
    return output


def test_rechecks_context_without_claiming_offline_dcap(tmp_path, monkeypatch):
    bundle = bundle_fixture(tmp_path, monkeypatch)
    result = evidence.verify(bundle, "ear", "policy")
    assert result["result"] == "PASS"
    assert result["offline_dcap"] == result["fresh_admission"] == "NOT_RUN"
    assert result["admission"]["status"] == "NOT_ESTABLISHED"


def test_changed_original_is_failure(tmp_path, monkeypatch):
    bundle = bundle_fixture(tmp_path, monkeypatch)
    (bundle / "plugin/quote.bin").write_bytes(b"changed")
    assert evidence.verify(bundle, "ear", "policy")["result"] == "FAIL"


def test_incomplete_capture_is_unknown(tmp_path):
    atomic(tmp_path / "manifest.json", {"schema": "argus.admission-bundle.v1", "files": {}})
    result = evidence.verify(tmp_path, "missing", "missing")
    assert result["result"] == "UNKNOWN" and "plugin/ear.jwt" in result["missing"]


def test_production_verifier_rejection_not_promoted(tmp_path, monkeypatch):
    bundle = bundle_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(evidence, "_run", lambda args: (_ for _ in ()).throw(ValueError("EAR signature rejected")))
    assert evidence.verify(bundle, "ear", "policy")["result"] == "FAIL"


def test_fixed_reference_matches_original_and_same_target(tmp_path, monkeypatch):
    bundle = bundle_fixture(tmp_path, monkeypatch)
    request = json.loads((bundle / "trustee/history-request.json").read_text())
    target = {k: v for k, v in request["runtime_data"].items() if k not in ("nonce", "protocol")}
    reference = tmp_path / "reference.json"
    atomic(reference, {"schema": "argus.fixed-approved-measurement.v1", "reference_bundle": str(bundle),
           "bundle_manifest_sha256": sha(bundle / "manifest.json"), "rtmr2": request["rtmr2"], "target": target,
           "nonce": request["runtime_data"]["nonce"], "quote_sha256": sha(bundle / "plugin/quote.bin"),
           "policy_sha256": sha(bundle / "approved-policy.rego")})
    assert approved_measurement(reference, request)["rtmr2"] == request["rtmr2"]
    request["runtime_data"]["container_id"] = "f" * 64
    with pytest.raises(ValueError, match="same target"):
        approved_measurement(reference, request)


def test_ear_only_cannot_become_approved_live_reference(tmp_path, monkeypatch):
    bundle = bundle_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="legal admission observation"):
        approve_reference(bundle, "ear", "policy", tmp_path / "reference.json")


def test_fixed_reference_rejects_policy_changed_under_same_id(tmp_path, monkeypatch):
    bundle = bundle_fixture(tmp_path, monkeypatch)
    request = json.loads((bundle / "trustee/history-request.json").read_text())
    reference = tmp_path / "reference.json"
    atomic(reference, {"schema": "argus.fixed-approved-measurement.v1", "reference_bundle": str(bundle),
           "bundle_manifest_sha256": sha(bundle / "manifest.json"), "rtmr2": request["rtmr2"],
           "target": {k: v for k, v in request["runtime_data"].items() if k not in ("nonce", "protocol")},
           "nonce": request["runtime_data"]["nonce"], "quote_sha256": sha(bundle / "plugin/quote.bin"),
           "policy_sha256": sha(bundle / "approved-policy.rego"), "approved_at_ms": 0})
    comparison = tmp_path / "comparison"
    shutil.copytree(bundle, comparison)
    (comparison / "approved-policy.rego").write_text("different approved policy with unchanged policy ID")
    config = json.loads((comparison / "verification.json").read_text())
    config["approved_policy_artifact"]["sha256"] = sha(comparison / "approved-policy.rego")
    atomic(comparison / "verification.json", config)
    manifest = json.loads((comparison / "manifest.json").read_text())
    for name in ("approved-policy.rego", "verification.json"):
        manifest["files"][name] = sha(comparison / name)
    atomic(comparison / "manifest.json", manifest)
    assert evidence.verify(comparison, "ear", "policy")["result"] == "PASS"
    with pytest.raises(ValueError, match="same approved policy bytes"):
        replay_bundle(comparison, reference, "ear", "policy", tmp_path / "result.json")


def test_matching_observation_establishes_conservative_admission_time(tmp_path, monkeypatch):
    bundle = bundle_fixture(tmp_path, monkeypatch)
    initial = evidence.verify(bundle, "ear", "policy")
    status = {"ready": True, "target_serial": "123", "helper_invocation_id": "helper-start"}
    observed_at = initial["captured_at_ms"] + 1000
    observation = {"schema": "argus.e1-observation.v1", "run_id": "trial-1", "registered_target": initial["target"],
                   "accepted_workload_nonce": initial["nonce"], "actual_admission": "ADMITTED",
                   "target_check": {"result": "MATCH"}, "status_before": status, "status_after": status,
                   "target_id": "spiffe://test/service", "completed_at_ms": observed_at,
                   "production_verification": {"target": initial["target"], "svid_and_business": {
                       "server_serial": "123", "server_spiffe_id": "spiffe://test/service"}}}
    atomic(bundle / "observation.json", observation)
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["files"]["observation.json"] = sha(bundle / "observation.json")
    atomic(bundle / "manifest.json", manifest)
    verified = evidence.verify(bundle, "ear", "policy")
    assert verified["admission"]["status"] == "OBSERVED_ADMITTED"
    assert verified["admission"]["run_id"] == "trial-1"
    assert verified["admission"]["observed_at_ms"] == observed_at
    reference = approve_reference(bundle, "ear", "policy", tmp_path / "reference.json")
    assert reference["quote_sha256"] == sha(bundle / "plugin/quote.bin")
