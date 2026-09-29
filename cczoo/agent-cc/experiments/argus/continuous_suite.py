"""Prepare the existing runner for paired continuous Agent tasks.

Each run names a provisioned Gateway/user configuration. This module does not
create users, start containers, or turn a weaker policy on in a production unit.
"""
from pathlib import Path

from common import atomic, digest, read, require, resolve
from runner import plan, validate
from variants import inspect_variant, SHORT


def generate(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    c = read(source)
    require(not output.exists(), "suite output must be new")
    groups = c.get("groups", ["full_argus", "native_spire_guarded"])
    require(groups and len(set(groups)) == len(groups)
            and set(groups) <= {"full_argus", "native_spire_guarded"}, "continuous main comparison uses Full/native")
    seeds = c.get("seeds", list(range(10)))
    conditions = c.get("conditions", ["fault", "no_fault"])
    fault_kind = c.get("fault_kind", "helper-freeze")
    require(fault_kind in ("helper-freeze", "helper-crash", "target-exit"),
            "continuous v1 selects one existing service fault; client faults retain fleet_fault")
    require(c.get("fault_scope", "shared_service") == "shared_service",
            "continuous local-client fault integration is NOT_RUN; fleet_fault is a separate availability diagnostic")
    require(conditions and len(set(conditions)) == len(conditions)
            and set(conditions) <= {"fault", "no_fault"}, "invalid continuous conditions")
    arms, artifacts, inputs, protocols, users, secrets = {}, [], {}, {}, set(), set()
    scale = None
    for group in groups:
        variant = resolve(source.parent, c["variant_outputs"][group])
        report = inspect_variant(variant)
        require(report["variant"] == group, "wrong server variant")
        server = read(variant / "environment.json")
        suffix = "/experiment/" + c["experiment_id"] + "/" + SHORT[group]
        arms[group] = {"identity_namespace": suffix, "registration_namespace": suffix,
                       "state_dir": server["paths"]["records_dir"], "variant_output": str(variant),
                       "artifacts": [str(variant / "variant.json")]}
        artifacts += arms[group]["artifacts"]
        for seed in seeds:
            for condition in conditions:
                path = resolve(source.parent, c["continuous_configs"][group][str(seed)][condition])
                config = read(path)
                require(config.get("schema") == "argus.continuous.v1", "invalid continuous config")
                require(config.get("group") == group and config.get("condition") == condition
                        and config.get("structure_seed") == seed, "continuous group/condition/structure seed mismatch")
                require(config.get("fault_kind", fault_kind) == fault_kind, "paired runs must select the same fault kind")
                require(config.get("fault_scope", "shared_service") == "shared_service"
                        and not config.get("injected_client_id") and not config.get("uninjected_client_ids"),
                        "shared service runs cannot declare uninjected clients")
                bindings = config["bindings"]
                scale = len(bindings) if scale is None else scale
                require(scale > 0 and len(bindings) == scale, "paired runs need equal client counts")
                require(len({b["client_id"] for b in bindings}) == scale, "duplicate continuous client")
                require(len({b["container"] for b in bindings}) == scale, "one independent Gateway per client")
                for binding in bindings:
                    require(binding["server_spiffe_id"] == server["identity"]["target_id"]
                            and binding["client_spiffe_id"] in server["identity"]["allowed_client_ids"],
                            "continuous identity does not match exact server allowlist")
                    user = (binding["account_id"], binding["user_id"])
                    require(user not in users, "each planned run needs fresh private users, including no-fault controls")
                    users.add(user)
                require("secret_seed" in config, "freeze a distinct private secret seed for each run")
                secret_hash = digest(config["secret_seed"])
                require(secret_hash not in secrets, "paired runs must not disclose the same generated secrets")
                secrets.add(secret_hash)
                schedule = config.get("schedule", {})
                formal = c.get("mode", "pilot") == "formal"
                require(not formal or schedule.get("frozen") is True, "freeze two engineering pilots before formal capture")
                controls = config.get("controls", {})
                if formal:
                    require(config.get("mode") == "formal" and config.get("model_settings", {}).get("model"),
                            "formal per-run configuration must declare formal mode and actual model")
                    require(config.get("fault_kind") == fault_kind, "formal run must declare the suite fault kind")
                    require(condition != "fault" or all(controls.get(k, {}).get("argv") for k in ("fault", "recovery")),
                            "formal fault runs require actual fault and recovery commands")
                # Commands/identities differ by arm; the offered load and event
                # times must not. Secret values never enter this public digest.
                protocol = digest({"schedule": schedule, "qa_timeout_seconds": config.get("qa_timeout_seconds", 120),
                                   "model_settings": config.get("model_settings", {}),
                                   "client_ids": sorted(b["client_id"] for b in bindings),
                                   "control_times": {k: v.get("at_s") for k, v in controls.items()}})
                require(seed not in protocols or protocols[seed] == protocol,
                        "paired runs must have identical schedule, client labels, and control times")
                protocols[seed] = protocol
                key = f"{group}:{scale}:{seed}:{condition}"
                inputs[key] = str(path)
                artifacts.append(str(path))
    tool = str(Path(__file__).with_name("step.py"))
    budget = c.get("run_timeout_s", 7200)
    require(type(budget) is int and 60 < budget <= 86400, "bounded continuous run timeout required")
    operation = {"id": "continuous", "role": "client", "kind": "mutation", "timeout_s": budget,
                 "argv": ["python3", tool, "continuous", "run", "--config", "{continuous_config}",
                          "--output", "{run_dir}", "--timeout", str(budget - 30)],
                 "resume_argv": ["python3", tool, "continuous", "resume", "--config", "{continuous_config}",
                                 "--output", "{run_dir}", "--timeout", str(budget - 30)],
                 "operation_id_file": "continuous/operation-id.json", "result": "step-result.json", "verdict_field": "result"}
    m = {"schema": "argus.experiment.v1", "experiment_id": c["experiment_id"], "groups": groups,
         "seeds": seeds, "scales": [scale], "scope": "private_user_memories", "arms": arms,
         "policy": {"can_reattest": False, "source": "unchanged approved server artifact"},
         "artifacts": artifacts, "secrets": {}, "continuous_inputs": inputs,
         "cases": [{"name": "continuous", "experiment": "E4", "conditions": conditions, "fault_kind": fault_kind,
                    "fault_scope": "shared_service", "operations": [operation]}],
         "local_continuous_fault": "NOT_RUN",
         "continuous_protocols": {str(k): v for k, v in protocols.items()}, "capture_mode": c.get("mode", "pilot")}
    validate(m)
    output.mkdir(parents=True)
    atomic(output / "suite.json", m)
    atomic(output / "run-order.json", {"runs": plan(m), "remote_acceptance": "NOT_RUN",
                                       "note": "Deploy the selected run's users/Gateway config before run --run-id; preserve volume only within that run."})
    return m
