#!/usr/bin/env python3
"""Bounded, explicit experiment-only config and container-incarnation faults.

Never accepts a shell snippet or Docker exec command. Unknown mutation outcomes
are retained, never replayed. A same-container restart replaces the process and
network/listener incarnation; it is NOT an isolated listener-port mutation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

from common import digest, require

EXPERIMENT_ROOT = Path("/srv/argus-experiments")
MAX_CONFIG = 4 * 1024 * 1024


def load_runtime(config_file):
    from trusted_runtime import load
    return load(config_file, require_experiment=True)


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def protected(path, *, experiment=False):
    path = Path(path)
    require(path.is_absolute(), "absolute protected file required")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o022 == 0,
            "fault input must be a root-owned regular file without group/world write")
    require(path.resolve() == path, "fault input must not pass through symlinks")
    for parent in path.parents:
        mode = parent.stat()
        require(mode.st_uid == 0 and mode.st_mode & 0o022 == 0,
                "fault input parent must be root-owned without group/world write")
    if experiment:
        require(path.is_relative_to(EXPERIMENT_ROOT) and path != EXPERIMENT_ROOT,
                "fault configuration must stay beneath /srv/argus-experiments")
    return path


def config_bytes(path, expected):
    require(isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected), "explicit SHA256 required")
    path = protected(path)
    require(path.stat().st_size <= MAX_CONFIG, "configuration exceeds audit size limit")
    raw = path.read_bytes()
    require(sha_bytes(raw) == expected, "configuration digest differs from declared fixture")
    require(isinstance(json.loads(raw), dict), "OpenViking fixture must be a JSON object")
    return raw


def same_instance(target):
    pid = str(target["pid"])
    require(re.fullmatch(r"[1-9][0-9]*", pid), "invalid target PID")
    proc = Path("/proc") / pid
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    require(fields[0] not in ("Z", "X") and fields[19] == target["start_time"], "target process changed or exited")
    require(Path("/proc/sys/kernel/random/boot_id").read_text().strip() == target["boot_id"], "target boot changed")
    for suffix, key in (("ns/pid", "pid_namespace"), ("ns/net", "net_namespace"), ("exe", "executable")):
        require(os.readlink(proc / suffix) == target[key], "target namespace or executable changed")
    require(target["container_id"] in (proc / "cgroup").read_text(), "target cgroup changed")


def view_path(target):
    require(Path(target["config_path"]).is_absolute() and ".." not in Path(target["config_path"]).parts,
            "invalid registered configuration path")
    return Path("/proc") / str(target["pid"]) / "root" / target["config_path"].lstrip("/")


def mutate_inode(path, target, old_digest, replacement):
    """A Docker file bind mount keeps the old inode across an atomic rename."""
    import fcntl
    same_instance(target)
    host = protected(path, experiment=True)
    observed = view_path(target)
    fd = os.open(host, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        actual, inside = os.fstat(fd), observed.stat()
        require((actual.st_dev, actual.st_ino) == (inside.st_dev, inside.st_ino),
                "host config is not the file bound into this target")
        original = os.read(fd, MAX_CONFIG + 1)
        require(len(original) <= MAX_CONFIG and sha_bytes(original) == old_digest,
                "current bound configuration differs; mutation not issued")
        same_instance(target)
        os.lseek(fd, 0, os.SEEK_SET)
        remaining = memoryview(replacement)
        while remaining:
            written = os.write(fd, remaining)
            require(written > 0, "configuration write did not progress")
            remaining = remaining[written:]
        os.ftruncate(fd, len(replacement))
        os.fsync(fd)
        require(sha_bytes(observed.read_bytes()) == sha_bytes(replacement), "target did not observe replacement config")
    finally:
        os.close(fd)


class Journal:
    def __init__(self, path, existing=False):
        import fcntl
        self.path = Path(path)
        # Resume is not a mutation command. Restore opens only the existing
        # root-owned record; default creation never overwrites a prior intent.
        if existing:
            protected(self.path)
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
        flags |= 0 if existing else os.O_CREAT | os.O_EXCL
        self.fd = os.open(self.path, flags, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if existing:
                require(self.path.read_bytes().endswith(b"\n"),
                        "fault checkpoint is truncated; reconcile it before any restore mutation")
        except BaseException:
            os.close(self.fd)
            raise

    def save(self, record):
        raw = memoryview((json.dumps(record, separators=(",", ":")) + "\n").encode())
        while raw:
            count = os.write(self.fd, raw)
            require(count > 0, "fault checkpoint did not progress")
            raw = raw[count:]
        os.fsync(self.fd)

    def close(self):
        os.close(self.fd)


def actual_target(workload, deployment):
    return json.loads(workload.run([deployment.bin / "argus-workload", "-action", "check", "-registration", deployment.target]))


def inspect(workload, cid):
    require(re.fullmatch("[0-9a-f]{64}", cid), "full checked container ID required")
    rows = json.loads(workload.run(["docker", "inspect", cid]))
    require(len(rows) == 1 and rows[0].get("Id") == cid, "Docker inspect identity mismatch")
    row = rows[0]
    labels = row.get("Config", {}).get("Labels", {})
    return {"container_id": row["Id"], "launch_id": labels.get("io.trucon.launch-id"),
            "workload_id": labels.get("io.trucon.workload-id"), "image_config_digest": row["Image"],
            "started_at": row.get("State", {}).get("StartedAt"), "running": row.get("State", {}).get("Running")}


def inject(args):
    require(args.execute_fault and args.hold_recovery, "explicit --execute-fault --hold-recovery required")
    require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", args.run_id), "fixed experiment run ID required")
    workload, observer, c = load_runtime(args.config)
    deployment = workload.Deployment(c)
    host_config = protected(deployment.workload["config_host_path"], experiment=True)
    target = actual_target(workload, deployment)
    require(target["workload_id"] == deployment.workload["id"] and target["agent_id"] == deployment.identity["agent_id"],
            "registered target differs from experiment deployment")
    record = {"type": "fault", "schema_version": 1, "run_id": args.run_id, "event": args.event,
              "target": target, "config_sha256": digest(c), "started_at_ms": observer.now_ms(),
              "executed": False, "phase": "prepared", "remote_acceptance": "NOT_RUN"}
    replacement = None
    if args.event == "config-change":
        replacement = config_bytes(args.replacement, args.replacement_sha256)
        original = config_bytes(args.original, args.original_sha256)
        require(args.original_sha256 != args.replacement_sha256, "replacement must change configuration bytes")
        require(host_config not in (Path(args.original).resolve(), Path(args.replacement).resolve()), "fixture sources must be separate from live configuration")
        live_stat = host_config.stat()
        require(all((live_stat.st_dev, live_stat.st_ino) != (Path(source).stat().st_dev, Path(source).stat().st_ino)
                    for source in (args.original, args.replacement)), "fixture sources cannot alias the live config inode")
        require(sha_bytes(host_config.read_bytes()) == args.original_sha256 and
                target["config_digest"] == "sha256:" + args.original_sha256 and
                c["approved"]["config_digest"] == target["config_digest"], "original configuration is not the approved current target")
        record["configuration"] = {"host_path": str(host_config), "original_path": str(Path(args.original).resolve()),
                                   "replacement_path": str(Path(args.replacement).resolve()),
                                   "original_sha256": sha_bytes(original), "replacement_sha256": sha_bytes(replacement)}
    elif args.event == "same-container-restart":
        before = inspect(workload, target["container_id"])
        require(all(before[key] == target[key] for key in ("container_id", "launch_id", "workload_id", "image_config_digest")) and before["running"],
                "restart target is not the checked live instance")
        record.update(before=before, scope="same container ID; process and network/listener incarnation replacement, not isolated port change")
    else:
        raise ValueError("unsupported fixture event")
    journal = Journal(args.output)
    try:
        owner = uuid.uuid4().hex
        record["recovery_hold"] = {"verified": False, "owner": owner, "path": str(observer.hold_path(deployment)),
                                   "sha256": sha_bytes(observer.hold_contents(owner)), "restart": "no"}
        journal.save(record)
        record["recovery_hold"] = observer.hold_recovery(deployment, workload.run, owner)
        require(actual_target(workload, deployment) == target, "target changed before fault intent")
        record.update(phase="mutation_intent", started_at_ms=observer.now_ms(),
                      started_monotonic_ns=time.monotonic_ns())
        journal.save(record)
        if args.event == "config-change":
            mutate_inode(host_config, target, args.original_sha256, replacement)
        else:
            # Exact fixed operation only. Preserve the configured Docker/Docktap
            # environment; never introduce arbitrary exec or an alternate socket.
            result = subprocess.run(["docker", "restart", "--time", "0", target["container_id"]],
                                    capture_output=True, timeout=30, check=False)
            require(result.returncode == 0, "container restart did not confirm success")
            after = inspect(workload, target["container_id"])
            record["after"] = after
            require(all(after[key] == record["before"][key] for key in ("container_id", "launch_id", "workload_id", "image_config_digest")) and
                    after["running"] and after["started_at"] != record["before"]["started_at"], "new same-container incarnation not observed")
        record.update(phase="mutation_observed", completed_at_ms=observer.now_ms(), executed=True)
        journal.save(record)
    except BaseException as error:
        record.update(phase="mutation_unknown" if record["phase"] == "mutation_intent" else "precondition_failed",
                      error_class=type(error).__name__, completed_at_ms=observer.now_ms())
        journal.save(record)
        raise
    finally:
        journal.close()
    return record


def restore(args):
    require(args.execute_restore, "explicit --execute-restore required")
    workload, observer, c = load_runtime(args.config)
    deployment = workload.Deployment(c)
    journal = Journal(args.fault, existing=True)
    try:
        record = observer.fault_checkpoint(args.fault)
        require(record.get("event") == "config-change" and record.get("config_sha256") == digest(c), "restore deployment/fault mismatch")
        require(record.get("phase") not in ("restore_intent", "restored", "restore_unknown"), "restore already attempted; reconcile it without replay")
        values, target = record["configuration"], record["target"]
        host = protected(values["host_path"], experiment=True)
        require(str(host) == deployment.workload["config_host_path"], "restore path differs from deployment")
        original = config_bytes(values["original_path"], values["original_sha256"])
        # Both source files remain verifiable; nothing is restored from log text.
        config_bytes(values["replacement_path"], values["replacement_sha256"])
        same_instance(target)
        current = sha_bytes(host.read_bytes())
        require(current in (values["original_sha256"], values["replacement_sha256"]),
                "unexpected current configuration; manual reconciliation required")
        record.update(phase="restore_intent", restore_started_at_ms=observer.now_ms())
        journal.save(record)
        try:
            mutate_inode(host, target, current, original)
            record.update(phase="restored", restored_at_ms=observer.now_ms(), restored_sha256=values["original_sha256"])
            journal.save(record)
        except BaseException as error:
            record.update(phase="restore_unknown", restore_error_class=type(error).__name__)
            journal.save(record)
            raise
    finally:
        journal.close()
    return {"restored": True, "run_id": record["run_id"], "admission": "NOT_RUN",
            "recovery_hold": "still installed; use remote_acceptance release after observations"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("inject")
    for field in ("config", "run-id", "output"):
        p.add_argument("--" + field, required=True)
    p.add_argument("--event", choices=("config-change", "same-container-restart"), required=True)
    for field in ("original", "replacement", "original-sha256", "replacement-sha256"):
        p.add_argument("--" + field)
    p.add_argument("--execute-fault", action="store_true")
    p.add_argument("--hold-recovery", action="store_true")
    p = commands.add_parser("restore")
    for field in ("config", "fault"):
        p.add_argument("--" + field, required=True)
    p.add_argument("--execute-restore", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0, "fault fixture requires Linux root on the experiment service host")
    result = inject(args) if args.command == "inject" else restore(args)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
