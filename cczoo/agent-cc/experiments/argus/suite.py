#!/usr/bin/env python3
"""Generate a functional suite; explicitly select steady-api for E5 measurements.

Server arms must already have been rendered by variants.py. Per-arm business
configuration references provisioned non-root user keys, never key material.
E1/E2/E3 have explicit evidence tools and scenario contracts in scenarios.json;
they are added after their deployment-specific milestones are configured.
"""
import argparse
import copy
from pathlib import Path
import sys

from common import GROUPS, atomic, digest, read, require, resolve
from variants import inspect_variant, SHORT
from runner import validate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters/OpenClaw/spiffe_client"))
import fleet
import deploy


def generate(config_file, output):
    source, output = Path(config_file).resolve(), Path(output).resolve()
    c = read(source)
    require(not output.exists(), "suite output must be new")
    selected_cases = c.get("cases", ["private-memory"])
    require(isinstance(selected_cases, list) and selected_cases and len(selected_cases) == len(set(selected_cases))
            and set(selected_cases) <= {"private-memory", "steady-api", "locomo"}, "select private-memory, steady-api or locomo")
    require(not {'private-memory', 'locomo'} <= set(selected_cases), 'LoCoMo needs separate users and a dedicated read-only evaluation Gateway')
    locomo = 'locomo' in selected_cases
    if locomo:
        require(len(c.get('seeds', [0])) == 1, 'each LoCoMo repetition needs fresh users in a separate deployment')
        require(30 < c.get('locomo_timeout_s', 86400) <= 86400, 'LoCoMo runner budget must exceed 30 seconds and be at most one day')
    measure = "steady-api" in selected_cases
    if measure:
        require(c.get("load", {}).get("frozen") is True, "freeze pilot load before steady-api measurements")
    base_fleet = read(resolve(source.parent, c["fleet_config"]))
    fleet.validate_fleet(base_fleet)
    groups = c.get("groups", ["full_argus"])
    require(groups and len(groups) == len(set(groups)) and set(groups) <= set(GROUPS), "invalid groups")
    numeric_ids = [item[key] for item in base_fleet['instances'] for key in ('gateway_uid', 'gateway_gid', 'reader_gid')]
    stride = max(numeric_ids) - min(numeric_ids) + 1
    output.mkdir(parents=True)
    arms, artifacts, scopes = {}, [], set()
    locomo_protocol = None
    for group_index, group in enumerate(groups):
        arm_path = resolve(source.parent, c["variant_outputs"][group])
        report = inspect_variant(arm_path)
        require(report["variant"] == group, "wrong server variant")
        server = read(arm_path / "environment.json")
        base_business = read(resolve(source.parent, c["business_configs"][group]))
        business_by_name = {i["name"]: i for i in base_business["instances"]}
        f = copy.deepcopy(base_fleet)
        f["node"]["server_spiffe_id"] = server["identity"]["target_id"]
        clients = server["identity"]["allowed_client_ids"]
        suffix = "/experiment/" + c["experiment_id"] + "/" + SHORT[group]
        directory = output / group; directory.mkdir()
        business = copy.deepcopy(base_business)
        business["instances"] = []
        if business.get("wrong_identity"):
            business["wrong_identity"]["server_spiffe_id"] = server["identity"]["target_id"]
        loads = []
        for item in f["instances"]:
            original = item["name"]
            item["name"] = (SHORT[group] + "-" + original)
            require(len(item["name"]) <= 23, "shorten instance name for isolated unit/group")
            item["container_name"] = c["experiment_id"] + "-" + item["name"]
            item["client_spiffe_id"] += suffix
            require(item["client_spiffe_id"] in clients, "client ID is absent from exact server allowlist")
            for key in ("gateway_uid", "gateway_gid", "reader_gid"):
                item[key] += group_index * stride
            user = copy.deepcopy(business_by_name[original]); user["name"] = item["name"]
            scope = (user["account_id"], user["user_id"])
            require(scope not in scopes, "use separate per-arm private business users to avoid cross-arm memory contamination")
            scopes.add(scope); business["instances"].append(user)
            credentials = "/run/argus-openclaw/instances/" + item["name"] + "/credentials/"
            route = c.get('load', {}).get('path', '/api/v1/system/status')
            require(route.startswith('/api/v1/') and not route.startswith('//'), 'load path must use the protected API')
            loads.append({"name": item["name"], "url": item["openviking_origin"].rstrip("/") + route,
                          "server_id": server["identity"]["target_id"], "credentials_dir": credentials.rstrip('/'),
                          "client_id": item['client_spiffe_id'], "api_key_file": user["api_key_file"]})
            body_file = c.get('load', {}).get('body_files', {}).get(original, c.get('load', {}).get('body_file'))
            if body_file:
                loads[-1]['body_file'] = str(resolve(source.parent, body_file))
        fleet.validate_fleet(f)
        fleet_file, business_file, load_file = directory / "fleet.json", directory / "business.json", directory / "load.json"
        business["deployment_config"] = str(fleet_file)
        atomic(fleet_file, f); atomic(business_file, business)
        if measure:
            atomic(load_file, dict(c["load"], instances=loads))
        arms[group] = {"identity_namespace": suffix, "registration_namespace": suffix,
                       "state_dir": server["paths"]["records_dir"], "variant_output": str(arm_path),
                       "fleet_config": str(fleet_file), "business_config": str(business_file)}
        group_artifacts = [str(fleet_file), str(business_file), str(arm_path / "variant.json")]
        if locomo:
            from locomo_run import configuration
            locomo_source = resolve(source.parent, c['locomo_configs'][group])
            lc, fixture, bindings, _ = configuration(locomo_source)
            by_container = {i['container_name']: i for i in f['instances']}
            by_user = {i['name']: i for i in business['instances']}
            require(set(b['container'] for b in bindings.values()) == set(by_container), 'LoCoMo bindings must cover generated evaluation Gateways')
            for binding in bindings.values():
                instance = by_container[binding['container']]; user = by_user[instance['name']]
                require(binding['docker_user'] == f"{instance['gateway_uid']}:{instance['gateway_gid']}"
                        and binding['client_spiffe_id'] == instance['client_spiffe_id']
                        and binding['server_spiffe_id'] == server['identity']['target_id']
                        and binding['config_path'] == '/home/node/.openclaw/openclaw.json'
                        and binding['agent_id'] == user.get('agent_id', 'main')
                        and (binding['account_id'], binding['user_id']) == (user['account_id'], user['user_id']), 'LoCoMo operational binding differs from generated deployment')
            protocol = digest({'fixture': fixture, 'poll_attempts': lc.get('poll_attempts', 120),
                               'poll_seconds': lc.get('poll_seconds', 2), 'qa_timeout_seconds': lc.get('qa_timeout_seconds', 180)})
            require(locomo_protocol is None or locomo_protocol == protocol, 'LoCoMo arms must use identical selected tasks and execution budgets')
            locomo_protocol = protocol
            lc['fixture'] = str(resolve(locomo_source.parent, lc['fixture']))
            locomo_file = directory / 'locomo.json'; atomic(locomo_file, lc)
            arms[group]['locomo_config'] = str(locomo_file)
            group_artifacts += [str(locomo_file), lc['fixture']]
        if measure:
            arms[group]["load_config"] = str(load_file)
            group_artifacts.append(str(load_file))
            group_artifacts += sorted({item['body_file'] for item in loads if item.get('body_file')})
        if group == "static_mtls":
            static = c.get("static_clients")
            require(isinstance(static, dict) and set(static) == {"binary", "binary_sha256", "instances"},
                    "static arm requires dedicated static client credentials and publisher build")
            require(set(static["instances"]) == {i["name"] for i in base_fleet["instances"]}, "static credentials must cover every independent client")
            static_config = dict(static, schema="argus.static-clients.v1", fleet_config=str(fleet_file),
                                 instances={"static-" + name: value for name, value in static["instances"].items()})
            from static_clients import select
            for instance in static_config["instances"]:
                select(static_config, instance)
            static_file = directory / "static-clients.json"
            atomic(static_file, static_config)
            arms[group]["static_clients_config"] = str(static_file)
            group_artifacts.append(str(static_file))
        arms[group]["artifacts"] = group_artifacts
        artifacts += group_artifacts
    tool = str(Path(__file__).with_name("step.py"))
    business_op = {"id": "private-memory", "role": "client", "kind": "mutation", "timeout_s": 16000,
                   "argv": ["python3", tool, "fleet", "run", "--config", "{business_config}", "--output", "{run_dir}"],
                   "resume_argv": ["python3", tool, "fleet", "resume", "--config", "{business_config}", "--output", "{run_dir}"],
                   "operation_id_file": "business/result.json", "result": "step-result.json", "verdict_field": "result"}
    load_op = {"id": "api-load", "role": "client", "kind": "measure", "timeout_s": c.get("load", {}).get("warmup_seconds", 30) + c.get("load", {}).get("measurement_seconds", 120) + 90,
               "argv": ["python3", tool, "load", "run", "--config", "{load_config}", "--output", "{run_dir}", "--clients", "{scale}"],
               "result": "step-result.json", "verdict_field": "result"}
    if not c.get('load', {}).get('body_file') and not c.get('load', {}).get('body_files'):
        load_op['safe_retry'] = 'get-measurement'
    locomo_op = {"id": "locomo", "role": "client", "kind": "mutation", "timeout_s": c.get('locomo_timeout_s', 86400),
                 "argv": ["python3", tool, "locomo", "run", "--config", "{locomo_config}", "--output", "{run_dir}", "--timeout", str(c.get('locomo_timeout_s', 86400) - 30)],
                 "resume_argv": ["python3", tool, "locomo", "resume", "--config", "{locomo_config}", "--output", "{run_dir}", "--timeout", str(c.get('locomo_timeout_s', 86400) - 30)],
                 "operation_id_file": "locomo/state.json", "result": "step-result.json", "verdict_field": "result"}
    m = {"schema": "argus.experiment.v1", "experiment_id": c["experiment_id"], "groups": groups,
         "seeds": c.get("seeds", [0]), "scales": c.get("scales", [1, 2, 4, 8] if measure else [len(base_fleet["instances"])]), "scope": "private_user_memories",
         "policy": {"can_reattest": False, "source": "unchanged approved server artifact"},
         "artifacts": artifacts, "arms": arms, "secrets": {}, "cases": []}
    if "private-memory" in selected_cases:
        m["cases"].append({"name": "private-memory", "experiment": "E4", "scales": [len(base_fleet["instances"])], "operations": [business_op]})
    if locomo:
        m['cases'].append({'name': 'locomo', 'experiment': 'E4', 'scales': [len(base_fleet['instances'])], 'operations': [locomo_op]})
    if measure:
        m["load"] = c["load"]
        m["cases"].append({"name": "steady-api", "experiment": "E5", "seeds": c.get("performance_seeds", [101, 102, 103, 104, 105]), "operations": [load_op]})
    validate(m); atomic(output / "suite.json", m)
    return m


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True); p.add_argument("--output", required=True)
    a = p.parse_args(); generate(a.config, a.output)


if __name__ == "__main__": main()
