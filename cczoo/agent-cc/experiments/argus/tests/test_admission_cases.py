from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_cases import CASES, fixture_case


@pytest.mark.parametrize("case", CASES)
def test_actual_signed_fixture_rules_and_explicit_outer_boundary(tmp_path, case):
    result = fixture_case(tmp_path / case, case)
    assert result["fixture_result"] == "PASS"
    assert result["full_admission"] == "NOT_RUN"
    rules = result["rules"]
    if case == "hidden_stop":
        assert rules["target_launch_only"]["decision"] == "ALLOW"
        assert rules["full_history_current_instance"]["decision"] == "DENY"
    if case == "unrelated_activity":
        assert rules["fixed_accumulated_measurement"]["decision"] == "DENY"
        assert rules["full_history_current_instance"]["decision"] == "ALLOW"
    if case in ("config_mismatch", "old_evidence"):
        assert result["outer_checks_required"]
