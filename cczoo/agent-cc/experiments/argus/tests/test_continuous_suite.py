from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import continuous_suite
from common import atomic, read
from runner import plan


def build_source(tmp_path, monkeypatch):
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
    build_source(tmp_path, monkeypatch)
    m = continuous_suite.generate(tmp_path / "source.json", tmp_path / "out")
    runs = plan(m)
    assert runs == plan(m) and len(runs) == 4 and len({r["run_id"] for r in runs}) == 4
    assert len({r["block_id"] for r in runs}) == 1
    assert {r["condition"] for r in runs} == {"fault", "no_fault"}
    assert m["cases"][0]["operations"][0]["operation_id_file"] == "continuous/operation-id.json"
    assert {r['fault_scope'] for r in runs} == {'shared_service'}
    assert m['local_continuous_fault'] == 'NOT_RUN'


@pytest.mark.parametrize("change", ["user", "secret", "schedule", "model", "formal", "fault_scope", "uninjected", "scenario"])
def test_continuous_rejects_contamination_and_unpaired_protocol(tmp_path, monkeypatch, change):
    source = build_source(tmp_path, monkeypatch)
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
    if change == 'scenario': control['scenario'] = 'work-item-v1'
    atomic(path, control); atomic(tmp_path / "source.json", source)
    with pytest.raises(ValueError): continuous_suite.generate(tmp_path / "source.json", tmp_path / "out")


def test_work_item_suite_keeps_six_step_denominator(tmp_path, monkeypatch):
    source = build_source(tmp_path, monkeypatch)
    for group in source['groups']:
        for condition, path in source['continuous_configs'][group]['0'].items():
            value = read(path)
            value['scenario'] = 'work-item-v1'
            value['controls']['fault']['at_s'] = 120
            value['controls']['recovery']['at_s'] = 240
            atomic(path, value)
    suite = continuous_suite.generate(tmp_path/'source.json', tmp_path/'out')
    assert suite['cases'][0]['planned_tasks'] == 6
    assert {r['planned_tasks'] for r in plan(suite)} == {6}
    assert {r['scenario'] for r in plan(suite)} == {'work-item-v1'}


@pytest.mark.parametrize('change',['valid','scenario','missing_condition','budget'])
def test_single_work_item_paper_profile_enforces_the_four_cell_protocol(tmp_path,monkeypatch,change):
    source=build_source(tmp_path,monkeypatch)
    source['profile']='argus-single-work-item-v1'
    for group in source['groups']:
        for condition,path in source['continuous_configs'][group]['0'].items():
            config=read(path); config.update(scenario='work-item-v1',max_concurrency=1,queue_limit=1,task_retries=0)
            config['controls']['fault']['at_s']=120
            config['controls']['recovery']['at_s']=240
            atomic(path,config)
    path=source['continuous_configs']['full_argus']['0']['fault']
    config=read(path)
    if change=='scenario': config['scenario']='ledger-v1'
    if change=='budget': config['task_retries']=1
    if change=='missing_condition': source['conditions']=['fault']
    atomic(path,config); atomic(tmp_path/'source.json',source)
    if change=='valid':
        suite=continuous_suite.generate(tmp_path/'source.json',tmp_path/'out')
        assert suite['profile']=='argus-single-work-item-v1' and len(plan(suite))==4
    else:
        with pytest.raises(ValueError): continuous_suite.generate(tmp_path/'source.json',tmp_path/'out')
