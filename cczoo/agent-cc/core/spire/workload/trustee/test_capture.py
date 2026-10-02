"""Optional capture failures must not become admission policy failures."""
import json
import io
from pathlib import Path
from types import SimpleNamespace

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
    # Exercise the production fetch/read counters, with only HTTP replaced.
    del verifier.fetch
    def open_response(query, timeout):
        reference = query.full_url.rsplit('/', 1)[-1]
        response = io.BytesIO(json.dumps({reference:raw[reference]}).encode())
        response.status = 200
        return response
    verifier.opener = SimpleNamespace(open=open_response)
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
    timing = json.loads((root / 'history-timing.json').read_text())
    assert timing['requested_entries'] == len(request['rekor_entry_ids'])
    assert timing['fetched_entries'] == len(request['rekor_entry_ids'])
    assert timing['fetched_bytes'] > 0 and timing['verify_ms'] >= timing['fetch_ms'] >= 0
    assert 'history-timing.json' in result['artifacts']
    # Timings stay in observer capture, outside authenticated verdict semantics.
    assert set(json.loads((root / 'history-result.json').read_text())['verdict']) == {'verified','baseline_rtmr','launch_entry_uuid'}


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
