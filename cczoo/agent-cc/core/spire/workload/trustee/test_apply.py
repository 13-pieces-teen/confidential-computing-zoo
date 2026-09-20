import importlib.util
from pathlib import Path

import pytest


def test_patch_refuses_unreviewed_source(tmp_path):
    spec = importlib.util.spec_from_file_location("trustee_apply", Path(__file__).with_name("apply.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "attestation-service/src/lib.rs"
    source.parent.mkdir(parents=True)
    source.write_text("pub mod config;\n")
    with pytest.raises(ValueError, match="differs"):
        module.apply(tmp_path)
    assert source.read_text() == "pub mod config;\n"
    assert not source.with_name("argus_trucon.rs").exists()
