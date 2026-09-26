"""Signed Merkle/DSSE fixtures exercise the unmodified production LogVerifier."""
import copy
from pathlib import Path
import sys
import json
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_diagnostics import ArchiveTransport, diagnose, production, trust_artifacts, validate_archive
from common import digest, sha
production()
from test_verify_trucon import fixture


def test_real_crypto_history_and_unrelated_activity(tmp_path):
    verifier, request, raw = fixture(tmp_path)
    result = diagnose(verifier, request, "0" * 96)
    assert result["rules"]["fixed_accumulated_measurement"]["decision"] == "DENY"
    assert result["rules"]["target_launch_only"]["decision"] == "ALLOW"
    assert result["rules"]["full_history_current_instance"]["decision"] == "ALLOW"
    assert result["live_admission"] == "NOT_RUN"


def test_target_launch_inclusion_misses_later_stop(tmp_path):
    verifier, request, raw = fixture(tmp_path, stop=True)
    result = diagnose(verifier, request, request["rtmr2"])
    assert result["rules"]["target_launch_only"]["decision"] == "ALLOW"
    assert result["rules"]["full_history_current_instance"]["decision"] == "DENY"


def test_signature_corruption_not_treated_as_valid_archived_history(tmp_path):
    verifier, request, raw = fixture(tmp_path, bad_dsse=True)
    result = diagnose(verifier, request, request["rtmr2"])
    assert result["rules"]["full_history_current_instance"]["decision"] == "DENY"
    assert result["rules"]["target_launch_only"]["decision"] == "DENY"


def test_current_instance_mismatch(tmp_path):
    verifier, request, raw = fixture(tmp_path)
    request["runtime_data"]["container_id"] = "f" * 64
    result = diagnose(verifier, request, request["rtmr2"])
    assert result["rules"]["full_history_current_instance"]["decision"] == "DENY"


def test_replay_pins_trust_file_bytes_not_only_config_path(tmp_path):
    verifier, request, raw = fixture(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(verifier.config))
    proof = tmp_path / "proof.json"
    proof.write_text("original context artifact")
    context = {"nonce": request["runtime_data"].get("nonce"), "request_sha256": digest(request),
               "verification_artifacts": {"proof.json": sha(proof)}}
    archive = {"schema": "argus.history-archive.v1", "config_sha256": sha(config),
               "request": request, "request_sha256": digest(request),
               "verification_context": context, "context_sha256": digest(context),
               "context_artifacts": {str(proof): sha(proof)}, "trust_artifacts": trust_artifacts(verifier.config)}
    validate_archive(config, archive)
    Path(verifier.config["rekor_public_key_path"]).write_text("different trust key")
    with pytest.raises(ValueError, match="trust material changed"):
        validate_archive(config, archive)


def test_log_replay_explicitly_does_not_claim_config_or_nonce_validation(tmp_path):
    verifier, request, _ = fixture(tmp_path)
    request["runtime_data"]["config_digest"] = "sha256:" + "f" * 64
    request["runtime_data"]["nonce"] = "different-from-original-challenge"
    result = diagnose(verifier, request, request["rtmr2"])
    assert result["rules"]["full_history_current_instance"]["decision"] == "ALLOW"
    assert "configuration admission policy" in result["rules"]["full_history_current_instance"]["not_checked_here"]
    assert result["live_admission"] == "NOT_RUN"
