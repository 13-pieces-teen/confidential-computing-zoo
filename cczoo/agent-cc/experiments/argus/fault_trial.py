#!/usr/bin/env python3
"""Two-host E2 coordinator. Run on the client; SSH uses an existing host alias.

Receiver runs independently on the server before this trial. Fault is issued once
after both traffic lanes AND the named application milestone. Resume only collects
existing evidence; it never restarts traffic, injects a second fault or recovers a
target automatically. Explicit release is separate in remote_acceptance.py.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time

from common import SAFE, atomic, digest, lock, read, require, sha, run_logged, sanitize_diagnostic
from runner import fault_ready
from client_material import resolve_credentials
import timeline

ROOT = Path(__file__).resolve().parents[2]
REMOTE_TOOL = ROOT / "core/spire/workload/scripts/remote_acceptance.py"


def fault_command(config):
    dedicated = config["event"] in ("config-change", "same-container-restart")
    argv = [config.get("server_python", "python3"), config["server_fault_fixture"] if dedicated else config["server_remote_acceptance"],
            "inject" if dedicated else "fault", "--config", config["server_deployment"], "--event", config["event"],
            "--run-id", config["run_id"], "--output", config["fault_file"], "--execute-fault", "--hold-recovery"]
    if config["event"] == "config-change":
        for key, value in config["fixture"].items():
            argv.extend(["--" + key.replace("_", "-"), value])
    return argv


def ssh_command(config, argv):
    require(SAFE.fullmatch(config["ssh_host"]), "use a configured SSH host alias")
    return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", config["ssh_host"], shlex.join([str(v) for v in argv])]


def remote(config, argv, *, timeout=40, output=None, diagnostic=None):
    command = ssh_command(config, argv)
    diagnostic = diagnostic or (Path(output).parent / 'remote-diagnostic.json' if output else Path(config['diagnostic_dir']) / 'remote-diagnostic.json')
    p = run_logged(command, stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                   diagnostic=diagnostic, stage='remote-command', timeout=timeout,
                   secrets=(os.environ.get(config.get('probe', {}).get('api_key_env', '')),))
    require(p.returncode == 0, "remote command failed; inspect server evidence")
    if output:
        path = Path(output)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(p.stdout)
        os.replace(temporary, path)


def lifecycle_config(config):
    value = config.get("timeline")
    if value is None:
        return None
    require(isinstance(value, dict), "timeline must be an observation configuration")
    require(set(value) <= {"server_tool", "output", "stop_file", "interval"}, "unknown timeline option")
    for key in ("server_tool", "output", "stop_file"):
        require(isinstance(value.get(key), str) and value[key].startswith("/"), "absolute timeline tool/evidence paths required")
    require(value["output"] != value["stop_file"] and value["output"] not in (config["fault_file"], config["receiver_file"])
            and value["stop_file"] not in (config["fault_file"], config["receiver_file"]), "observation files must have distinct fresh paths")
    require(.05 <= value.get("interval", .25) <= 5, "timeline interval must be in [.05,5] seconds")
    return value


def start_lifecycle(config, output):
    """A foreground SSH process, bounded in time; no daemon or service install."""
    observer = lifecycle_config(config)
    if observer is None:
        return None
    duration = config.get("baseline_budget_s", 60) + config["probe"].get("duration", 40) + 45
    require(1 <= duration <= 3600, "combined observation duration exceeds one hour")
    argv = [config.get("server_python", "python3"), observer["server_tool"], "observe", "--config", config["server_deployment"],
            "--run-id", config["run_id"], "--output", observer["output"], "--stop-file", observer["stop_file"],
            "--duration", str(duration), "--interval", str(observer.get("interval", .25))]
    child = subprocess.Popen(ssh_command(config, argv), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    tail = bytearray()
    def drain():
        while data := child.stderr.read(4096):
            tail.extend(data)
            del tail[:-16384]
    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    return {"child": child, "thread": thread, "tail": tail, "stopped": False, "ready": False}


def lifecycle_ready(config, output, handle):
    if handle is None:
        return True
    if handle["ready"]:
        return True
    require(handle["child"].poll() is None, "lifecycle observer ended before its healthy baseline")
    path = output / "lifecycle-live.jsonl"
    remote(config, ["cat", "--", config["timeline"]["output"]], output=path, timeout=5)
    rows = timeline.acceptance.probe_rows(path, config["run_id"])
    handle["ready"] = any(r.get("type") == "observer_ready" and r.get("run_id") == config["run_id"] for r in rows)
    return handle["ready"]


def finish_lifecycle(config, output, handle):
    if handle is None:
        return {"status": "NOT_RUN"}
    if handle["stopped"]:
        return handle["result"]
    failed = False
    child = handle["child"]
    try:
        if child.poll() is None:
            # This flag stops only this finite observer, never the application.
            remote(config, [config.get("server_python", "python3"), "-c",
                           "from pathlib import Path; import sys; Path(sys.argv[1]).touch(exist_ok=True)",
                           config["timeline"]["stop_file"]], timeout=10)
            child.wait(timeout=8)
        failed = child.returncode != 0
    except (OSError, ValueError, subprocess.SubprocessError):
        failed = True
    finally:
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=3)
        handle["thread"].join(timeout=1)
        handle["stopped"] = True
        handle["result"] = {"status": "UNKNOWN" if failed else "COMPLETED", "exit_code": child.returncode,
                            "healthy_baseline": handle["ready"]}
        atomic(output / "lifecycle-diagnostic.json", dict(handle["result"],
               stderr_tail=sanitize_diagnostic(bytes(handle["tail"]), (os.environ.get(config.get("probe", {}).get("api_key_env", "")),))))
    return handle["result"]


def collect(config, output):
    # Fixed declared metadata files, not arbitrary recursive host copying.
    # Each resume keeps a separate source snapshot so an earlier report's
    # hashes remain reproducible if the remote collector has appended records.
    sources = output / ("collection-" + str(time.time_ns()))
    sources.mkdir()
    current_result = output / "result.json"
    if current_result.exists():
        (sources / "previous-result.json").write_bytes(current_result.read_bytes())
    atomic(current_result, {"run_id": config["run_id"], "result": "UNKNOWN", "phase": "collection_incomplete",
                           "source_directory": sources.name,
                           "operation_id": os.environ.get("ARGUS_OPERATION_ID", digest(["fault", config["run_id"]])[:24])})
    (sources / "trace.jsonl").write_bytes((output / "trace.jsonl").read_bytes())
    for key, name in (("fault_file", "fault.jsonl"), ("receiver_file", "receiver.jsonl")):
        remote(config, ["cat", "--", config[key]], output=sources / name)
    assessment = output / ("assessment-" + str(time.time_ns()) + ".json")
    require(not assessment.exists(), "assessment output already exists")
    command = [sys.executable, str(REMOTE_TOOL), "check", "--trace", str(sources / "trace.jsonl"),
               "--fault", str(sources / "fault.jsonl"), "--receiver", str(sources / "receiver.jsonl"),
               "--bound-ms", str(config["bound_ms"]), "--clock-uncertainty-ms", str(config["clock_uncertainty_ms"]),
               "--output", str(assessment)]
    process = run_logged(command, diagnostic=output / 'assessment-diagnostic.json', stage='receiver-assessment', timeout=30)
    require(assessment.is_file(), "missing fresh assessment")
    result = read(assessment)
    require(result.get("run_id") == config["run_id"], "assessment belongs to another run")
    require(result.get("result") in ("PASS", "FAIL", "UNKNOWN"), "invalid assessment verdict")
    require(process.returncode == {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[result["result"]], "assessment exit/result mismatch")
    result.update(operation_id=os.environ.get("ARGUS_OPERATION_ID", digest(["fault", config["run_id"]])[:24]),
                  source_directory=sources.name,
                  sources_sha256={name: sha(sources / name) for name in ("trace.jsonl", "fault.jsonl", "receiver.jsonl")})
    events = []
    observation = "NOT_RUN"
    if lifecycle_config(config) is not None:
        try:
            remote(config, ["cat", "--", config["timeline"]["output"]], output=sources / "lifecycle.jsonl")
            events = timeline.acceptance.probe_rows(sources / "lifecycle.jsonl", config["run_id"])
            result["sources_sha256"]["lifecycle.jsonl"] = sha(sources / "lifecycle.jsonl")
            completed = [r for r in events if r.get("type") == "observer_stop" and r.get("run_id") == config["run_id"]]
            observation = "COMPLETE" if (len(completed) == 1 and completed[0].get("complete") is True
                and completed[0].get("healthy_baseline") is True and completed[0].get("failed_samples", 0) == 0
                and not any(r.get("type") in ("probe_gap", "observer_gap") for r in events)) else "UNKNOWN"
        except (OSError, ValueError, subprocess.SubprocessError):
            observation = "UNKNOWN"
    fault = timeline.acceptance.fault_checkpoint(sources / "fault.jsonl")
    summary = timeline.summarize(fault, timeline.acceptance.receiver_rows(sources / "receiver.jsonl", config["run_id"]),
                                timeline.acceptance.probe_rows(sources / "trace.jsonl", config["run_id"]), events,
                                bound_ms=config["bound_ms"], clock_uncertainty_ms=config["clock_uncertainty_ms"])
    summary["lifecycle_observation"] = observation
    summary["source_directory"] = sources.name
    summary["sources_sha256"] = dict(result["sources_sha256"])
    summary_path = output / ("timeline-" + str(time.time_ns()) + ".json")
    atomic(summary_path, summary)
    result.update(timeline_path=summary_path.name, timeline_sha256=sha(summary_path), lifecycle_observation=observation)
    atomic(output / "result.json", result)
    return result


def inflight_first_read(config, output):
    rows = [json.loads(line) for line in (output / 'trace.jsonl').read_text().splitlines() if line.strip()]
    starts = [r for r in rows if r.get('type') == 'stream_start' and r.get('run_id') == config['run_id']]
    if len(starts) != 1:
        return None
    request_id = starts[0]['request_id']
    if any(r.get('type') == 'request' and r.get('request_id') == request_id for r in rows):
        return None  # It already ended; a buffered completed POST is not in-flight.
    status_file = output / 'receiver-status.json'
    remote(config, ['env', 'PYTHONPATH=' + config['server_receiver_package'], config.get('server_python', 'python3'),
                   '-m', 'receiver_audit.collector', 'status', '--control', config['collector_control'],
                   '--request-id', request_id], timeout=10, output=status_file)
    value = read(status_file)
    row = value.get('first_read') or {}
    return row if (value.get('run_id') == config['run_id'] and row.get('request_id') == request_id
                   and row.get('received_body_bytes', 0) > 0 and row.get('phase') == 'body_read'
                   and row.get('provenance') == 'kernel_process_and_deployment') else None


def run(config_file, output, resume=False):
    config, output = read(config_file), Path(output).resolve()
    config['diagnostic_dir'] = str(output)
    require(SAFE.fullmatch(config.get("run_id", "")), "fixed run_id required")
    require(config.get("event") in ("helper-freeze", "helper-crash", "target-exit", "config-change", "same-container-restart"), "unsupported fault")
    if config["event"] in ("config-change", "same-container-restart"):
        require(isinstance(config.get("server_fault_fixture"), str) and config["server_fault_fixture"].startswith("/"), "server fault fixture path required")
    if config["event"] == "config-change":
        require(set(config.get("fixture", {})) == {"original", "replacement", "original_sha256", "replacement_sha256"}, "protected fixture references and hashes required")
    require(config.get("clock_uncertainty_ms", -1) >= 0 and config.get("bound_ms", 0) > 0, "measured clock uncertainty and declared bound required")
    lifecycle_config(config)
    with lock(output):
        state_path = output / "state.json"
        if resume:
            state = read(state_path)
            require(state["config_digest"] == digest(config), "fault trial configuration changed")
            result = collect(config, output)
            return result
        require(not state_path.exists(), "trial already started; use resume (collection only)")
        state = {"config_digest": digest(config), "run_id": config["run_id"], "phase": "probe_intent", "result": "UNKNOWN"}
        atomic(state_path, state)
        probe = dict(config["probe"])
        probe.update(resolve_credentials(probe, int((probe.get('duration', 40) + 5) * 1000)))
        argv = [sys.executable, str(REMOTE_TOOL), "probe", "--run-id", config["run_id"], "--output", str(output / "trace.jsonl")]
        for key in ("url", "cert", "key", "bundle", "server_id", "duration", "interval", "timeout", "method", "body_file", "api_key_env", "response_marker"):
            if key in probe:
                argv.extend(["--" + key.replace("_", "-"), str(probe[key])])
        require(probe.get("method") == "POST" and probe.get("body_file"), "delivery trial requires a synthetic body")
        inflight = probe.get('inflight', True)
        if inflight:
            argv.append('--inflight')
        state["phase"] = "observer_start_intent" if config.get("timeline") else "probe_intent"
        atomic(state_path, state)
        observer = start_lifecycle(config, output)
        try:
            child = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except BaseException:
            finish_lifecycle(config, output, observer)
            raise
        error_tail = bytearray()
        def drain_probe():
            while data := child.stderr.read(4096):
                error_tail.extend(data)
                del error_tail[:-16384]
        diagnostic_thread = threading.Thread(target=drain_probe, daemon=True)
        diagnostic_thread.start()
        state.update(phase="waiting_for_baseline", probe_pid=child.pid); atomic(state_path, state)
        try:
            deadline = time.monotonic() + config.get("baseline_budget_s", 60)
            while True:
                if child.poll() is not None or time.monotonic() >= deadline:
                    state.update(phase='not_run', result='NOT_RUN', reason='BASELINE_OR_INFLIGHT_READ_UNREACHABLE')
                    atomic(state_path, state)
                    atomic(output / 'result.json', dict(state, run_id=config['run_id'],
                           operation_id=os.environ.get('ARGUS_OPERATION_ID', digest(['fault', config['run_id']])[:24])))
                    return state
                try:
                    if not lifecycle_ready(config, output, observer):
                        time.sleep(.25)
                        continue
                    fault_ready(output / "trace.jsonl", config["milestone_file"], config["run_id"])
                    if inflight:
                        first_read = inflight_first_read(config, output)
                        if first_read is None:
                            time.sleep(.25)
                            continue
                        state['first_body_read'] = first_read
                    break
                except (ValueError, OSError):
                    time.sleep(.25)
            # Persist before SSH; timeout/interrupt must not issue a second fault.
            state["phase"] = "fault_submission_unknown"; atomic(state_path, state)
            remote(config, fault_command(config))
            state["phase"] = "observing"; atomic(state_path, state)
            child.wait(timeout=probe.get("duration", 40) + 35)
            require(child.returncode == 0, "probe incomplete")
            # Give periodic source watermarks a chance to cover the last probe
            # sample. This is outside the measured window, not a durability SLA.
            time.sleep(1)
            # Finalize preserves covered intervals and marks crash tails UNKNOWN.
            remote(config, ["env", "PYTHONPATH=" + config["server_receiver_package"], config.get("server_python", "python3"), "-m", "receiver_audit.collector", "finalize",
                            "--control", config["collector_control"]])
            state["lifecycle"] = finish_lifecycle(config, output, observer)
            atomic(state_path, state)
            result = collect(config, output)
            state.update(phase="collected", result=result["result"]); atomic(state_path, state)
            return result
        finally:
            finish_lifecycle(config, output, observer)
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
            diagnostic_thread.join(timeout=1)
            atomic(output / 'probe-diagnostic.json', {'stage':'connection-probe', 'exit_code':child.returncode,
                   'stderr_tail':sanitize_diagnostic(bytes(error_tail), (os.environ.get(probe.get('api_key_env', '')),))})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("run", "resume")); p.add_argument("--config", required=True); p.add_argument("--output", required=True)
    a = p.parse_args(); result = run(a.config, a.output, a.command == "resume")
    print(json.dumps(result, indent=2)); return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__": sys.exit(main())
