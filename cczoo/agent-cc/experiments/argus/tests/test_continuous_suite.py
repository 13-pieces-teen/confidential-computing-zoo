from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import continuous_suite
from common import atomic, read
from runner import plan


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(continuous_suite, "inspect_variant", lambda p: {"variant": p.name})
    groups = ["full_argus", "native_spire_guarded"]
    source = {"experiment_id": "ctest", "cases": ["continuous"], "groups": groups, "seeds": [0],
              "variant_outputs": {}, "continuous_configs": {}}
    for group in groups:
        variant = tmp_path / group
        atomic(variant / "environment.json", {"identity": {"target_id": "spiffe://test/"+group,
               "allowed_client_ids": ["spiffe://test/client/"+group]}, "paths": {"records_dir": str(variant/"records")}})
        source["variant_outputs"][group] = str(variant)
        source["continuous_configs"][group] = {"0": {}}
        for condition in ("fault", "no_fault"):
            path = tmp_path / (group + "-" + condition + ".json")
            atomic(path, {"schema": "argus.continuous.v1", "group": group, "condition": condition,
                "structure_seed": 0, "secret_seed": group+condition, "schedule": {"frozen": False},
                "controls": {"fault": {"at_s": 360, "argv": []}, "recovery": {"at_s": 720, "argv": []}},
                "bindings": [{"client_id": "c1", "container": group, "account_id": "default", "user_id": group+condition,
                  "client_spiffe_id": "spiffe://test/client/"+group, "server_spiffe_id": "spiffe://test/"+group}]})
            source["continuous_configs"][group]["0"][condition] = str(path)
    atomic(tmp_path / "source.json", source)
    return source


def test_conditions_are_paired_randomized_once_and_resumable(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    m = continuous_suite.generate(tmp_path / "source.json", tmp_path / "out")
    runs = plan(m)
    assert runs == plan(m) and len(runs) == 4 and len({r["run_id"] for r in runs}) == 4
    assert len({r["block_id"] for r in runs}) == 1
    assert {r["condition"] for r in runs} == {"fault", "no_fault"}
    assert m["cases"][0]["operations"][0]["operation_id_file"] == "continuous/operation-id.json"
    assert {r['fault_scope'] for r in runs} == {'shared_service'}
    assert m['local_continuous_fault'] == 'NOT_RUN'


@pytest.mark.parametrize("change", ["user", "secret", "schedule", "model", "formal", "fault_scope", "uninjected"])
def test_continuous_rejects_contamination_and_unpaired_protocol(tmp_path, monkeypatch, change):
    source = setup(tmp_path, monkeypatch)
    fault = read(source["continuous_configs"]["full_argus"]["0"]["fault"])
    path = source["continuous_configs"]["full_argus"]["0"]["no_fault"]
    control = read(path)
    if change == "user": control["bindings"][0]["user_id"] = fault["bindings"][0]["user_id"]
    if change == "secret": control["secret_seed"] = fault["secret_seed"]
    if change == "schedule": control["schedule"]["deadline_s"] = 1
    if change == "model": control["model_settings"] = {"model": "different"}
    if change == "formal": source["mode"] = "formal"
    if change == "fault_scope": control['fault_scope'] = 'local_client'
    if change == "uninjected": control['uninjected_client_ids'] = ['c1']
    atomic(path, control); atomic(tmp_path / "source.json", source)
    with pytest.raises(ValueError): continuous_suite.generate(tmp_path / "source.json", tmp_path / "out")
