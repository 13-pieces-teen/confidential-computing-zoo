#!/usr/bin/env python3
"""Archive admission originals and recheck their recorded verification context.

Reuses the production EAR checker, LogVerifier and Trustee's pinned Rego engine.
No offline DCAP, new challenge, identity issuance or live admission is performed.
"""
import argparse
import base64
import copy
import json
from pathlib import Path
import shutil
import subprocess
import time

from common import atomic, read, require, sha
from history_diagnostics import ArchiveTransport, production


def collect(plugin_capture, trustee_capture, config, output, observation=None):
    config_file = Path(config)
    config = read(config_file)
    required = {"ear_public_key_path", "ear_expected_issuer", "ear_expected_profile", "policy_id",
                "approved_policy_artifact", "history_config_sha256"}
    require(set(config) == required, "explicit EAR, policy and history trust configuration required")
    policy = config["approved_policy_artifact"]
    require(set(policy) == {"path", "sha256"} and sha(policy["path"]) == policy["sha256"],
            "approved policy bytes differ")
    output = Path(output).resolve()
    require(not output.exists(), "use a new bundle directory")
    output.mkdir(parents=True, mode=0o700)
    for prefix, source in (("plugin", Path(plugin_capture)), ("trustee", Path(trustee_capture))):
        require(source.is_dir(), "capture directory missing")
        names = ["capture.json"]
        if (source / "capture.json").is_file():
            names += list(read(source / "capture.json").get("artifacts", {}))
        names += ["local-check.json"] if prefix == "plugin" else ["policy-input.json"]
        for name in set(names):
            require(Path(name).name == name and name not in (".", ".."), "invalid capture filename")
            if (source / name).is_file():
                (output / prefix).mkdir(exist_ok=True, mode=0o700)
                shutil.copyfile(source / name, output / prefix / name)
                (output / prefix / name).chmod(0o600)
    shutil.copyfile(config["ear_public_key_path"], output / "ear-public-key.pem")
    shutil.copyfile(policy["path"], output / "approved-policy.rego")
    config["ear_public_key_path"] = "ear-public-key.pem"
    config["approved_policy_artifact"]["path"] = "approved-policy.rego"
    atomic(output / "verification.json", config)
    if observation:
        source = Path(observation)
        if source.is_dir():
            source = source / "observation.json"
        shutil.copyfile(source, output / "observation.json")
    manifest = {"schema": "argus.admission-bundle.v1", "collected_at_ms": time.time_ns() // 1000000,
                "verification_config_sha256": sha(config_file),
                "files": {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
                "scope": "captured originals; collection alone is not validation"}
    atomic(output / "manifest.json", manifest)
    return manifest


def _run(argv):
    result = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, "production verification command rejected captured input")
    return json.loads(result.stdout)


def _decode_request(raw):
    require(len(raw.get("verification_requests", [])) == 1, "expected one verification request")
    request = raw["verification_requests"][0]
    inner = json.loads(base64.urlsafe_b64decode(request["evidence"] + "=" * (-len(request["evidence"]) % 4)))
    require(request["tee"] == "tdx" and request["runtime_data_hash_algorithm"] == "sha384", "unexpected evidence protocol")
    return request["runtime_data"]["structured"], inner


def history_verifier(bundle):
    """Production verifier over captured transport and relocated public trust."""
    bundle = Path(bundle)
    capture = read(bundle / "trustee/capture.json")
    archived = read(bundle / "trustee/history-config.json")
    config = copy.deepcopy(archived)
    def relocated(path):
        require(path in capture["trust_files"], "missing captured trust material")
        name = capture["trust_files"][path]
        require(isinstance(name, str) and Path(name).name == name and name in capture["artifacts"],
                "captured trust material is not an archived artifact")
        return str(bundle / "trustee" / name)
    config["rekor_public_key_path"] = relocated(archived["rekor_public_key_path"])
    config["init_public_key_paths"] = [relocated(p) for p in archived.get("init_public_key_paths", [])]
    if archived.get("sigstore_trusted_root_path"):
        config["sigstore_trusted_root_path"] = relocated(archived["sigstore_trusted_root_path"])
    cls, _, _ = production()
    verifier = cls(config)
    verifier.opener = ArchiveTransport(read(bundle / "trustee/rekor.json"))
    return verifier


def _verify(bundle, verifier_bin, policy_bin):
    bundle = Path(bundle).resolve()
    manifest = read(bundle / "manifest.json")
    require(manifest.get("schema") == "argus.admission-bundle.v1", "unsupported admission bundle")
    for name, expected in manifest["files"].items():
        path = (bundle / name).resolve()
        require(path.is_relative_to(bundle), "bundle path escapes directory")
        require(sha(path) == expected, "bundle artifact changed: " + name)
    needed = ["verification.json", "ear-public-key.pem", "approved-policy.rego", "plugin/capture.json",
              "plugin/evidence.json", "plugin/request.json", "plugin/quote.bin", "plugin/ear.jwt",
              "plugin/local-check.json", "trustee/capture.json", "trustee/history-request.json",
              "trustee/history-config.json", "trustee/history-result.json", "trustee/rekor.json", "trustee/policy-input.json"]
    missing = [name for name in needed if name not in manifest["files"]]
    if missing:
        return {"result": "UNKNOWN", "missing": missing}
    config, captured = read(bundle / "verification.json"), read(bundle / "plugin/capture.json")
    history_capture = read(bundle / "trustee/capture.json")
    require(captured.get("schema") == "argus.admission-export.v1" and history_capture.get("schema") == "argus.trucon-export.v1",
            "unsupported original capture")
    for prefix, capture in (("plugin", captured), ("trustee", history_capture)):
        for name, expected in capture["artifacts"].items():
            require(manifest["files"].get(prefix + "/" + name) == expected, "original capture hash changed")
    evidence = read(bundle / "plugin/evidence.json")
    data = evidence["runtime_data"]
    nonce = data["nonce"]
    require(captured["nonce"] == history_capture["nonce"] == nonce, "capture nonce mismatch")
    require(config["policy_id"] == data["policy_id"], "policy identity mismatch")
    require(sha(bundle / "approved-policy.rego") == config["approved_policy_artifact"]["sha256"], "approved policy changed")
    require(sha(bundle / "trustee/history-config.json") == config["history_config_sha256"], "history trust configuration differs")
    submitted = read(bundle / "plugin/request.json")
    require(submitted.get("policy_ids") == [config["policy_id"]], "submitted appraisal policy differs")
    runtime, inner = _decode_request(submitted)
    require(runtime == data and inner["rekor_entry_ids"] == evidence["rekor_entry_ids"], "submitted request differs from evidence")
    quote = (bundle / "plugin/quote.bin").read_bytes()
    require(quote == base64.b64decode(inner["quote"], validate=True)
            == base64.urlsafe_b64decode(evidence["quote"] + "=" * (-len(evidence["quote"]) % 4)), "raw Quote differs from request")
    ear = _run([verifier_bin, "--evidence", bundle / "plugin/evidence.json", "--ear", bundle / "plugin/ear.jwt",
                "--key", bundle / "ear-public-key.pem", "--issuer", config["ear_expected_issuer"],
                "--profile", config["ear_expected_profile"], "--policy", config["policy_id"],
                "--captured-at-ms", captured["captured_at_ms"]])
    require(ear.get("result") == "PASS", "EAR checker did not confirm verification")
    target = {k: v for k, v in data.items() if k not in ("protocol", "nonce")}
    local = read(bundle / "plugin/local-check.json")
    require(local["nonce"] == nonce and local["target"] == target and local["matched"] is True,
            "post-appraisal local target check did not match")
    history = read(bundle / "trustee/history-request.json")
    require(history["runtime_data"] == data and history["rekor_entry_ids"] == evidence["rekor_entry_ids"], "history request association differs")
    if history_capture.get("coverage") != "COMPLETE":
        return {"result": "UNKNOWN", "reason": "history export is incomplete", "target": target, "nonce": nonce}
    verifier = history_verifier(bundle)
    try:
        verdict = verifier.verify(history)
    except Exception as error:
        # Production cryptographic libraries use several exception classes.
        # Archived transport has no live network path; rejection is explicit.
        raise ValueError("production history verifier rejected captured history: " + type(error).__name__) from error
    original_result = read(bundle / "trustee/history-result.json")
    require(original_result["result"] == "ALLOW" and original_result["verdict"] == verdict, "history verdict differs from capture")
    policy_input = read(bundle / "trustee/policy-input.json")
    require(policy_input["runtime_data_claims"] == data and policy_input["tdx"]["trucon"] == verdict
            and policy_input["tdx"]["quote"]["body"]["rtmr_2"] == history["rtmr2"], "policy input association differs")
    policy = _run([policy_bin, bundle / "approved-policy.rego", bundle / "trustee/policy-input.json"])
    token = (bundle / "plugin/ear.jwt").read_text().split(".")
    signed = json.loads(base64.urlsafe_b64decode(token[1] + "=" * (-len(token[1]) % 4)))["submods"]["cpu0"]
    expected_claims = signed.get("ear.trustworthiness-vector")
    if expected_claims is None:
        return {"result": "UNKNOWN", "reason": "signed EAR has no trustworthiness vector for policy replay comparison",
                "target": target, "nonce": nonce, "checks": {"ear": "PASS", "history": "PASS", "local_target": "PASS", "policy": "UNKNOWN"}}
    claims = policy.get("trust_claims")
    require(isinstance(claims, dict) and claims and isinstance(expected_claims, dict)
            and all(expected_claims.get(k) == v for k, v in claims.items()), "replayed policy claims differ from signed EAR")
    admission = {"status": "NOT_ESTABLISHED"}
    if "observation.json" in manifest["files"]:
        observation = read(bundle / "observation.json")
        live = observation.get("production_verification", {})
        proof = live.get("svid_and_business", {})
        status = observation.get("status_after", {})
        require(observation.get("schema") == "argus.e1-observation.v1", "unsupported admission observation")
        require(observation.get("accepted_workload_nonce") == nonce and observation.get("registered_target") == target
                and live.get("target") == target, "observation targets a different appraisal")
        if (observation.get("actual_admission") == "ADMITTED" and observation.get("target_check", {}).get("result") == "MATCH"
                and observation.get("status_before") == status and status.get("ready") is True
                and proof.get("server_serial") == status.get("target_serial")
                and proof.get("server_spiffe_id") == observation.get("target_id")
                and observation["completed_at_ms"] >= captured["captured_at_ms"]):
            admission = {"status": "OBSERVED_ADMITTED", "observed_at_ms": observation["completed_at_ms"], "target": target,
                         "run_id": observation.get("run_id"), "target_id": observation.get("target_id"),
                         "helper_invocation_id": status.get("helper_invocation_id"), "server_serial": proof["server_serial"]}
    return {"result": "PASS", "target": target, "nonce": nonce, "captured_at_ms": captured["captured_at_ms"],
            "checks": {"ear": "PASS", "history": "PASS", "policy": "PASS", "local_target": "PASS",
                       "observation": "PASS" if admission["status"] == "OBSERVED_ADMITTED" else "NOT_RUN"},
            "admission": admission}


def verify(bundle, verifier_bin, policy_bin):
    """Return explicit evidence completeness; callers never infer live admission."""
    try:
        result = _verify(bundle, verifier_bin, policy_bin)
    except (OSError, subprocess.TimeoutExpired, ImportError) as error:
        result = {"result": "UNKNOWN", "reason": type(error).__name__}
    except (ValueError, KeyError, TypeError, IndexError) as error:
        result = {"result": "FAIL", "reason": str(error)}
    result.update(schema="argus.admission-verification.v1", offline_dcap="NOT_RUN", fresh_admission="NOT_RUN",
                  scope="production EAR checks at capture time, archived history and policy replay; trusted capture provenance")
    manifest = Path(bundle) / "manifest.json"
    result["bundle_manifest_sha256"] = sha(manifest) if manifest.is_file() else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("collect")
    for name in ("plugin-capture", "trustee-capture", "config", "output"):
        command.add_argument("--" + name, required=True)
    command.add_argument("--observation")
    command = sub.add_parser("verify")
    for name in ("bundle", "verifier-bin", "policy-bin", "output"):
        command.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if args.command == "collect":
        collect(args.plugin_capture, args.trustee_capture, args.config, args.output, args.observation)
        print(json.dumps({"result": "COLLECTED", "verification": "NOT_RUN"}))
    else:
        result = verify(args.bundle, args.verifier_bin, args.policy_bin)
        atomic(args.output, result)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
