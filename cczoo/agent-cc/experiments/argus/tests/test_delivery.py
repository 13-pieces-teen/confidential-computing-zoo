from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery
from common import atomic


def test_portable_source_hash_preserves_raw_snapshot_and_rejects_edits(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    code = source / "entry.py"
    code.write_bytes(b"print('synthetic')\r\n")
    manifest = tmp_path / "source-manifest.json"
    with patch.object(delivery, "ROOT", tmp_path), patch.object(delivery, "SCOPES", ["src"]):
        atomic(manifest, {"schema": "argus.source-delivery.v1",
                         "source_sha256_normalization": "CRLF to LF only; no other whitespace changes",
                         "files": delivery.files(manifest, normalize_newlines=True),
                         "workspace_files_sha256": delivery.files(manifest)})
        assert delivery.verify(manifest)["original_workspace_bytes_match"] is True
        code.write_bytes(b"print('synthetic')\n")
        result = delivery.verify(manifest)
        assert result["result"] == "PASS"
        assert result["original_workspace_bytes_match"] is False
        code.write_bytes(b"print('modified')\n")
        with pytest.raises(ValueError, match="source manifest differs"):
            delivery.verify(manifest)


def test_build_context_rules_are_part_of_source_manifest(tmp_path):
    (tmp_path / ".dockerignore").write_text("private/\n", encoding="utf-8")
    with patch.object(delivery, "ROOT", tmp_path), patch.object(delivery, "SCOPES", [".dockerignore"]):
        assert ".dockerignore" in delivery.files(tmp_path / "source-manifest.json")


def test_runtime_outputs_do_not_change_source_manifest_but_fixtures_do(tmp_path):
    for relative in ('experiments/argus/private/data.json', 'experiments/argus/evidence/result.json',
                     'experiments/argus/generated/config.json', 'experiments/argus/examples/config.json',
                     'experiments/argus/tests/fixtures/evidence/result.json'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')
    with patch.object(delivery, 'ROOT', tmp_path), patch.object(delivery, 'SCOPES', ['experiments/argus']):
        actual = delivery.files(tmp_path / 'source-manifest.json')
    assert set(actual) == {'experiments/argus/examples/config.json',
                           'experiments/argus/tests/fixtures/evidence/result.json'}
