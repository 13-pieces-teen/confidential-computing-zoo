"""Optional capture failures must not become admission policy failures."""
import json
from pathlib import Path

import verify_trucon
from verify_trucon import export_capture, export_capture_async
from test_verify_trucon import fixture


def test_default_capture_writes_nothing(tmp_path):
    verifier, request, raw = fixture(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(verifier.config))
    before = set(tmp_path.iterdir())
    export_capture(None, request, config, verifier, verifier.verify(request))
    assert set(tmp_path.iterdir()) == before


def test_capture_contains_actual_history_and_trust_bytes(tmp_path):
    verifier, request, raw = fixture(tmp_path)
    verifier.capture_entries = {key: {key: value} for key, value in raw.items()}
    config = tmp_path / "config.json"
    config.write_text(json.dumps(verifier.config))
    export_capture(str(tmp_path / "capture"), request, config, verifier, verifier.verify(request))
    root = tmp_path / "capture" / request["runtime_data"]["nonce"]
    result = json.loads((root / "capture.json").read_text())
    assert result["coverage"] == "COMPLETE"
    assert json.loads((root / "history-request.json").read_text()) == request
    assert json.loads((root / "rekor.json").read_text()) == verifier.capture_entries
    for original, stored in result["trust_files"].items():
        assert Path(original).read_bytes() == (root / stored).read_bytes()


def test_export_failure_does_not_raise_or_change_verdict(tmp_path):
    verifier, request, _ = fixture(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(verifier.config))
    bad = tmp_path / "not-directory"
    bad.write_text("file")
    result = verifier.verify(request)
    export_capture(str(bad), request, config, verifier, result)
    assert result["verified"] is True


def test_async_parent_does_not_wait_or_write(tmp_path, monkeypatch):
    verifier, request, _ = fixture(tmp_path)
    monkeypatch.setattr(verify_trucon.sys, "platform", "linux")
    monkeypatch.setattr(verify_trucon.os, "fork", lambda: 12345, raising=False)
    export_capture_async(str(tmp_path / "capture"), request, tmp_path / "unused", verifier, {"verified": True})
    assert not (tmp_path / "capture").exists()


def test_optional_capture_unsupported_platform_does_not_raise(tmp_path, monkeypatch):
    verifier, request, _ = fixture(tmp_path)
    monkeypatch.setattr(verify_trucon.sys, "platform", "win32")
    export_capture_async(str(tmp_path / "capture"), request, tmp_path / "unused", verifier, {"verified": True})
    assert not (tmp_path / "capture").exists()
