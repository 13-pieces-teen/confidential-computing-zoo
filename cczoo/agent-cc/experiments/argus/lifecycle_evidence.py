#!/usr/bin/env python3
"""E3 snapshots, independent Docker create stream, and narrow measurements.

Snapshot and comparison commands are read-only. The explicit time-ready-command
wrapper executes the supplied operator command once without recovery/replay.
Remote freshness/Quote measurements remain separate.
"""
import argparse
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import sys
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

from common import append, atomic, digest, read, require, sha


def now_ms():
    return time.time_ns() // 1000000


def runtime(config_file):
    from trusted_runtime import load
    workload, _, c = load(config_file)
    return workload, c


def capture(config_file):
    workload, c = runtime(config_file)
    d = workload.Deployment(c)
    result = {"schema": "argus.lifecycle-snapshot.v1", "started_at_ms": now_ms(),
              "config_sha256": digest(c), "workload_id": d.workload["id"],
              "target_id": d.identity["target_id"], "hardware_acceptance": "NOT_RUN"}
    installed = json.loads(workload.protected_file(d.install / 'build-manifest.json').read_text())
    result['runtime_variant'] = installed.get('experiment_variant', 'full_argus')
    first = workload.status(c)
    result["status"] = first
    if d.target.exists():
        result["target"] = json.loads(workload.run([d.bin / "argus-workload", "-action", "check", "-registration", d.target]))
    state_path = d.records / "launch-state.json"
    if state_path.exists():
        state = json.loads(workload.protected_file(state_path).read_text())
        result["launch"] = {key: state.get(key) for key in ("run_id", "launch_id", "container_id", "stage", "config_sha256")}
    ids = workload.run(["docker", "ps", "-aq", "--no-trunc", "--filter", "label=io.trucon.workload-id=" + d.workload["id"]]).splitlines()
    require(all(re.fullmatch("[0-9a-f]{64}", value) for value in ids), "Docker inventory contains noncanonical container IDs")
    containers = json.loads(workload.run(["docker", "inspect", *ids])) if ids else []
    result["containers"] = [{"container_id": entry["Id"],
                             "launch_id": entry.get("Config", {}).get("Labels", {}).get("io.trucon.launch-id"),
                             "running": entry.get("State", {}).get("Running", False)} for entry in containers]
    if first["ready"]:
        cert, bundle = d.credentials / "current/svid.pem", d.credentials / "current/bundle.pem"
        before_bytes = cert.read_bytes()
        public = ssl._ssl._test_decode_cert(str(cert))
        validated = subprocess.run(["openssl", "verify", "-CAfile", str(bundle), "-untrusted", str(cert), str(cert)],
                                   capture_output=True, timeout=10, check=False)
        serial = str(int(public["serialNumber"], 16))
        uris = [value for kind, value in public.get("subjectAltName", ()) if kind == "URI"]
        require(validated.returncode == 0 and uris == [d.identity["target_id"]] and
                serial == first["target_serial"] and ssl.cert_time_to_seconds(public["notAfter"]) > time.time(),
                "public workload certificate does not match valid readiness")
        require(cert.read_bytes() == before_bytes and workload.status(c) == first,
                "readiness or certificate changed during snapshot; collect a new snapshot")
        result["credential"] = {"serial": serial, "expires_at": public["notAfter"], "uri_san": uris,
                                "certificate_sha256": hashlib.sha256(before_bytes).hexdigest(), "chain_valid": True}
    result["completed_at_ms"] = now_ms()
    return result


class DockerConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=5):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def quote_snapshot(socket_path, run_id=None):
    """Read actual hardware-generation counters from the existing Provider UDS."""
    if run_id is not None:
        require(isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', run_id), 'invalid run ID')
    started = now_ms()
    conn = DockerConnection(str(socket_path))
    try:
        conn.request("GET", "/ra/v1/quote-counters")
        response = conn.getresponse()
        raw = response.read(65537)
        require(response.status == 200 and len(raw) <= 65536, "Provider counters unavailable")
        value = json.loads(raw)
        require(value.get("schema") == "argus.quote-counters.v1", "unexpected Provider counter schema")
        require(value.get("provider_instance_id") and value.get("agent_id"), "Provider identity missing")
        for kind in ("node", "workload"):
            counts = value[kind]
            require(all(type(counts.get(k)) is int and counts[k] >= 0 for k in ("attempted", "generated", "failed")), "invalid Quote counters")
            require(counts["generated"] + counts["failed"] <= counts["attempted"], "inconsistent Quote counters")
        value.update(socket_path=str(socket_path), observation_started_at_ms=started, observation_completed_at_ms=now_ms())
        if run_id is not None:
            value['run_id'] = run_id
        return value
    finally:
        conn.close()


def quote_delta(before, after):
    result = {"result": "UNKNOWN", "scope": "actual Provider generation; includes discarded Quotes", "node": None, "workload": None,
              "generation_timing": {}}
    if (before.get("schema") != "argus.quote-counters.v1" or after.get("schema") != before.get("schema")
            or any(not before.get(k) or before[k] != after.get(k) for k in ("provider_instance_id", "agent_id", "socket_path"))
            or before.get("observation_completed_at_ms", float("inf")) > after.get("observation_started_at_ms", 0)):
        return result | {"reason": "Provider process/source changed or observation windows overlap"}
    for kind in ("node", "workload"):
        old, new = before.get(kind, {}), after.get(kind, {})
        if not all(type(x.get(k)) is int and x[k] >= 0 for x in (old, new) for k in ("attempted", "generated", "failed")):
            return result | {"reason": "generation counter missing"}
        if any(x["attempted"] != x["generated"] + x["failed"] for x in (old, new)):
            return result | {"reason": "Quote generation was in flight at a snapshot boundary"}
        delta = {k: new[k] - old[k] for k in ("attempted", "generated", "failed")}
        if min(delta.values()) < 0:
            return result | {"reason": "counter reset"}
        result[kind] = delta
        timing = {'result': 'UNKNOWN', 'scope': 'monotonic generate_quote calls, including failed and discarded attempts'}
        durations = [x.get('generation_elapsed_ns') for x in (old, new)]
        if all(type(value) is int and value >= 0 for value in durations) and durations[1] >= durations[0]:
            elapsed = durations[1] - durations[0]
            completed = delta['generated'] + delta['failed']
            timing.update(result='OBSERVED', completed_calls=completed, elapsed_ns=elapsed,
                          mean_ms=elapsed / completed / 1_000_000 if completed else None)
        result['generation_timing'][kind] = timing
    return result | {"result": "OBSERVED", "provider_instance_id": before["provider_instance_id"], "agent_id": before["agent_id"]}


def storage_ready(config_file, url, api_key_file, expected_content_sha256, run_id):
    """One read-only local-backend content probe, separate from ingress admission."""
    workload, config = runtime(config_file)
    deployment = workload.Deployment(config)
    target = json.loads(workload.run([deployment.bin / "argus-workload", "-action", "check", "-registration", deployment.target]))
    parsed = urlsplit(url)
    require(parsed.scheme == "http" and parsed.hostname and ipaddress.ip_address(parsed.hostname).is_loopback
            and not parsed.username and not parsed.password and not parsed.fragment
            and parsed.port == int(target["listen_port"]) and parsed.path == "/api/v1/content/read",
            "storage probe requires the local backend's content-read route and bound listen port")
    require(re.fullmatch(r"[0-9a-f]{64}", expected_content_sha256), "expected persisted content hash required")
    key = workload.protected_file(api_key_file).read_text().strip()
    require(key, "empty business key")
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    started = now_ms()
    result = {"schema": "argus.storage-ready.v1", "run_id": run_id, "target": target,
              "started_at_ms": started, "result": "UNKNOWN", "expected_content_sha256": expected_content_sha256,
              "scope": "local persisted-content read; not client ingress admission or Agent task success"}
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(Request(url, headers={"X-API-Key": key}), timeout=5) as response:
            raw = response.read(1_048_577)
            require(len(raw) <= 1_048_576, "storage probe response too large")
            value = json.loads(raw)
            content = value.get("result")
            require(response.status == 200 and value.get("status") == "ok" and isinstance(content, str), "storage read did not return content")
            actual = hashlib.sha256(content.encode()).hexdigest()
            result.update(result="PASS" if actual == expected_content_sha256 else "FAIL", observed_content_sha256=actual)
    except (OSError, ValueError, KeyError) as error:
        result["error_class"] = type(error).__name__
    result["completed_at_ms"] = now_ms()
    return result


def time_ready_command(config_file, argv_file, run_id, output, timeout_seconds=120, poll_interval=.25):
    """Execute one explicit operator command, then observe deployment readiness.

    This is command-to-observed-readiness wall cost, not Trustee processing time.
    An existing output is never resumed/replayed; unknown command results remain
    in the persisted record for the operator to reconcile.
    """
    require(isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', run_id), 'invalid run ID')
    require(0 < timeout_seconds <= 3600 and .05 <= poll_interval <= 5, 'invalid command/readiness timing limits')
    argv = read(argv_file)
    require(isinstance(argv, list) and argv and all(isinstance(s, str) and s for s in argv), 'argv file must contain a nonempty string array')
    path = Path(output)
    require(not path.exists(), 'timing output already exists; inspect the previous command outcome, do not replay it')
    workload, config = runtime(config_file)
    deployment = workload.Deployment(config)
    before = workload.status(config)
    result = {'schema': 'argus.command-readiness.v1', 'run_id': run_id, 'target_id': deployment.identity['target_id'],
              'agent_id': deployment.identity['agent_id'], 'config_sha256': digest(config), 'argv_file_sha256': sha(argv_file),
              'result': 'UNKNOWN', 'command_state': 'intent', 'automatic_retries': 0, 'initially_ready': before.get('ready') is True,
              'command_elapsed_ms': None, 'ready_observed_elapsed_ms': None,
              'scope': 'explicit command through first subsequent observed readiness; includes command, polling and local checks, not pure attestation cost'}
    atomic(path, result)
    started = time.perf_counter()
    result.update(command_started_at_ms=now_ms(), command_state='submitted_outcome_unknown')
    atomic(path, result)
    try:
        completed = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=timeout_seconds, check=False)
        result.update(command_state='completed', command_exit_code=completed.returncode,
                      command_completed_at_ms=now_ms(), command_elapsed_ms=(time.perf_counter()-started)*1000)
        atomic(path, result)
        if completed.returncode:
            result.update(result='FAIL', reason='explicit command failed; no retry executed')
            return result
        was_unready = not result['initially_ready']
        while time.perf_counter()-started < timeout_seconds:
            status = workload.status(config)
            was_unready |= status.get('ready') is not True
            # Do not attribute preexisting readiness to the command just measured.
            new_helper = status.get('helper_invocation_id') and status.get('helper_invocation_id') != before.get('helper_invocation_id')
            if status.get('ready') is True and (was_unready or new_helper):
                result.update(result='OBSERVED', ready_observed_at_ms=now_ms(),
                              ready_observed_elapsed_ms=(time.perf_counter()-started)*1000,
                              helper_invocation_id=status.get('helper_invocation_id'), target_serial=status.get('target_serial'))
                return result
            time.sleep(min(poll_interval, max(0, timeout_seconds-(time.perf_counter()-started))))
        result['reason'] = 'no new valid readiness observed within the measurement budget'
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        result.update(error_class=type(error).__name__, reason='command outcome or readiness unknown; no retry executed')
    finally:
        atomic(path, result)
    return result


def observe_creates(socket_path, workload_id, duration, output):
    require(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", workload_id), "invalid workload ID")
    require(1 <= duration <= 3600, "create observation duration must be in [1,3600]")
    path = Path(output)
    # Reserve without overwrite before opening any socket; no event body or environment is saved.
    os.close(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    until = math.ceil(time.time()) + duration
    filters = {"type": ["container"], "event": ["create"], "label": ["io.trucon.workload-id=" + workload_id]}
    conn = DockerConnection(socket_path, timeout=duration + 10)
    seq, complete = 0, False
    def emit(kind, **fields):
        nonlocal seq
        seq += 1
        append(path, {"schema": "argus.docker-creates.v1", "type": kind, "record_seq": seq,
                      "workload_id": workload_id, "at_ms": now_ms(), **fields})
    try:
        conn.request("GET", "/events?" + urlencode({"until": str(until), "filters": json.dumps(filters)}))
        response = conn.getresponse()
        require(response.status == 200, "Docker event subscription failed")
        peer = struct.unpack("3i", conn.sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(peer[1] == 0, "Docker create observer requires a root-owned host daemon socket")
        emit("create_observer_start", until_ms=until * 1000, daemon_peer_pid=peer[0], daemon_peer_uid=peer[1])
        print(json.dumps({"status": "CREATE_OBSERVER_READY", "workload_id": workload_id, "until_ms": until * 1000}), flush=True)
        while True:
            raw = response.readline(65537)
            if not raw:
                # The daemon's server-side `until` closes the completed stream.
                # Early EOF/socket timeout/process loss is never complete coverage.
                complete = now_ms() >= until * 1000
                break
            require(len(raw) <= 65536 and raw.endswith(b"\n"), "Docker event frame truncated or oversized")
            event = json.loads(raw)
            attrs = event.get("Actor", {}).get("Attributes", {})
            cid = event.get("Actor", {}).get("ID") or event.get("id")
            require(event.get("Type") == "container" and event.get("Action") == "create" and
                    attrs.get("io.trucon.workload-id") == workload_id and
                    isinstance(cid, str) and re.fullmatch("[0-9a-f]{64}", cid), "Docker event differs from requested stream")
            stamp = event.get("timeNano")
            require(type(stamp) is int and stamp > 0, "Docker event lacks creation timestamp")
            emit("container_create", container_id=cid, launch_id=attrs.get("io.trucon.launch-id"), created_at_ns=stamp)
    except Exception as error:
        complete = False
        emit("create_observer_error", error_class=type(error).__name__)
    finally:
        conn.close()
        emit("create_observer_stop", complete=complete, coverage_until_ms=until * 1000 if complete else None)
    return complete


def rotation(before, after):
    result = {"schema": "argus.lifecycle-result.v1", "case": "workload_svid_rotation", "result": "UNKNOWN",
              "node_quote_reuse": "NOT_RUN", "business_continuity": "NOT_RUN"}
    if not same_run(before, after):
        return result | {"reason": "snapshots differ in configuration, workload, target identity or time order"}
    if not before.get("status", {}).get("ready") or not after.get("status", {}).get("ready"):
        return result | {"reason": "both snapshots need valid current readiness"}
    if not before.get("target") or before["target"] != after.get("target") or not before["status"].get("helper_invocation_id") or before["status"]["helper_invocation_id"] != after["status"].get("helper_invocation_id"):
        return result | {"reason": "target or Helper changed; not a within-instance rotation observation"}
    a, b = before.get("credential", {}), after.get("credential", {})
    if not all(x.get("chain_valid") is True and x.get("uri_san") == [before["target_id"]] and
               x.get("serial") == snap["status"].get("target_serial") for x, snap in ((a, before), (b, after))):
        return result | {"reason": "validated certificate/readiness association missing"}
    if a.get("serial") == b.get("serial") or a.get("certificate_sha256") == b.get("certificate_sha256"):
        return result | {"reason": "no new public SVID observed"}
    return result | {"result": "PASS", "before_serial": a["serial"], "after_serial": b["serial"],
                     "scope": "same instance and Helper, valid public Workload SVID changed"}


def journal_anchor(config_file):
    workload, config = runtime(config_file)
    deployment = workload.Deployment(config)
    started = now_ms()
    rows = [json.loads(line) for line in workload.run(['journalctl', '-n', '1', '--no-pager', '-o', 'json']).splitlines() if line.strip()]
    require(len(rows) == 1 and rows[0].get('__CURSOR') and rows[0].get('_BOOT_ID'), 'journal anchor unavailable')
    return {'schema': 'argus.helper-journal-anchor.v1', 'config_sha256': digest(config),
            'helper_unit': deployment.unit('helper'), 'cursor': rows[0]['__CURSOR'],
            'boot_id': rows[0]['_BOOT_ID'], 'started_at_ms': started, 'completed_at_ms': now_ms()}


def journal_window(config_file, anchor_file):
    workload, config = runtime(config_file)
    deployment, anchor = workload.Deployment(config), read(anchor_file)
    require(anchor.get('schema') == 'argus.helper-journal-anchor.v1' and anchor.get('config_sha256') == digest(config)
            and anchor.get('helper_unit') == deployment.unit('helper'), 'journal anchor deployment mismatch')
    # Check the original cursor is still retained. Successful empty queries alone
    # cannot establish absence after a journal vacuum or boot change.
    boundary = [json.loads(line) for line in workload.run(['journalctl', '--cursor=' + anchor['cursor'], '-n', '1',
                                                         '--no-pager', '-o', 'json']).splitlines() if line.strip()]
    require(len(boundary) == 1 and boundary[0].get('__CURSOR') == anchor['cursor'], 'journal anchor no longer retained')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip().replace('-', '') == anchor['boot_id'], 'journal boot changed')
    ended = now_ms()
    raw = workload.run(['journalctl', '--after-cursor=' + anchor['cursor'], '-u', deployment.unit('helper'),
                        '--until=@%.3f' % (ended / 1000), '--no-pager', '-o', 'json'])
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return {'schema': 'argus.helper-journal.v1', 'config_sha256': digest(config), 'helper_unit': deployment.unit('helper'),
            'anchor': anchor, 'anchor_sha256': sha(anchor_file), 'completed_at_ms': now_ms(),
            'coverage_started_at_ms': anchor['completed_at_ms'], 'coverage_ended_at_ms': ended,
            'source': 'trusted_local_journal_cursor_query', 'records': rows}


def helper_events(archive, before, after):
    result = {'result': 'UNKNOWN', 'subscription_starts': None, 'subscription_ends': None,
              'svid_publications': None, 'distinct_svid_serials': None,
              'scope': 'trusted local journal events in the snapshot window; not Quote counts'}
    try:
        require(same_run(before, after), 'event snapshots differ')
        require(archive.get('schema') == 'argus.helper-journal.v1' and archive.get('config_sha256') == before['config_sha256']
                and archive.get('source') == 'trusted_local_journal_cursor_query', 'journal capture source differs')
        left, right = before['started_at_ms'], after['completed_at_ms']
        require(archive.get('coverage_started_at_ms', float('inf')) <= left and archive.get('coverage_ended_at_ms', 0) >= right,
                'journal query does not cover the snapshots')
        boot = before['target']['boot_id'].replace('-', '')
        require(after['target']['boot_id'].replace('-', '') == boot == archive['anchor']['boot_id'], 'journal boot differs')
        records, cursors, events = archive.get('records', []), set(), []
        targets = [s['target'] for s in (before, after)]
        for row in records:
            require(isinstance(row.get('__CURSOR'), str) and row['__CURSOR'] not in cursors, 'journal cursor missing or duplicated')
            cursors.add(row['__CURSOR'])
            require(row.get('_SYSTEMD_UNIT') == archive['helper_unit'] and row.get('_BOOT_ID') == boot, 'journal unit or boot mismatch')
            at = int(row['__REALTIME_TIMESTAMP']) / 1000
            if not left <= at <= right:
                continue
            message = row.get('MESSAGE', '')
            if not isinstance(message, str):
                continue
            kind = next((kind for prefix, kind in (('workload subscription ended ', 'end'),
                        ('workload subscription ', 'start'), ('target SVID published ', 'publish')) if prefix in message), None)
            if kind is None:
                continue
            pairs = re.findall(r'(?:^|\s)([a-z_][a-z0-9_]*)=([^\s"\\]+)', message)
            fields = dict(pairs)
            require(len(fields) == len(pairs), 'duplicate event field')
            subscription = fields.get('subscription_id')
            require(subscription and subscription == row.get('_SYSTEMD_INVOCATION_ID'), 'subscription invocation differs')
            matching = [t for t in targets if fields.get('launch_id') == t.get('launch_id')]
            if not matching:
                continue
            if kind != 'end':
                require(any(all(fields.get(k) == str(t.get(k)) for k in ('launch_id', 'container_id', 'pid', 'start_time'))
                            and fields.get('policy') == t.get('policy_id') for t in matching), 'event target differs from snapshots')
            if kind == 'publish':
                require(re.fullmatch(r'[0-9]+', fields.get('serial', '')), 'SVID publication lacks serial')
            events.append(dict(kind=kind, at_ms=at, subscription_id=subscription,
                               launch_id=fields.get('launch_id'), serial=fields.get('serial')))
        result.update(result='OBSERVED', subscription_starts=sum(r['kind'] == 'start' for r in events),
                      subscription_ends=sum(r['kind'] == 'end' for r in events),
                      svid_publications=sum(r['kind'] == 'publish' for r in events),
                      distinct_svid_serials=len({r['serial'] for r in events if r['kind'] == 'publish'}), events=events)
    except (KeyError, ValueError, TypeError) as error:
        result['reason'] = str(error)
    return result


def reestablished(before, after, kind, events, admission=None):
    """Resubscription and new controlled launch are distinct from rotation/resume."""
    result = {'schema': 'argus.lifecycle-result.v1', 'case': kind, 'result': 'UNKNOWN',
              'hardware_acceptance': 'NOT_RUN', 'admission': 'UNKNOWN'}
    try:
        require(kind in ('workload-resubscribe', 'replacement-launch') and same_run(before, after), 'snapshot/case association differs')
        require(before.get('runtime_variant', 'full_argus') == after.get('runtime_variant', 'full_argus'), 'runtime variant changed')
        a, b = before['target'], after['target']
        require(all(s.get('status', {}).get('ready') is True for s in (before, after)), 'both endpoint snapshots must be ready')
        old, new = [s['status'].get('helper_invocation_id') for s in (before, after)]
        require(old and new and old != new, 'new subscription invocation was not observed')
        require(events.get('result') == 'OBSERVED' and any(e['kind'] == 'start' and e['subscription_id'] == new for e in events['events'])
                and any(e['kind'] == 'publish' and e['subscription_id'] == new and e['serial'] == after['status'].get('target_serial') for e in events['events']),
                'new subscription and current SVID publication are not linked')
        for snap in (before, after):
            cred = snap.get('credential', {})
            require(cred.get('chain_valid') is True and cred.get('uri_san') == [snap['target_id']]
                    and cred.get('serial') == snap['status'].get('target_serial'), 'valid public SVID/readiness association missing')
        if kind == 'workload-resubscribe':
            require(a == b, 'same-instance subscription changed the target')
        else:
            require(a.get('launch_id') and b.get('launch_id') and a['launch_id'] != b['launch_id']
                    and a.get('container_id') != b.get('container_id') and b.get('container_id'), 'replacement needs a new controlled launch and container')
            launch = after.get('launch', {})
            require(launch.get('stage') == 'complete' and launch.get('config_sha256') == after['config_sha256']
                    and all(launch.get(k) == b.get(k) for k in ('launch_id', 'container_id')), 'new launch completion/target association missing')
        require(admission and admission.get('schema') == 'argus.e1-observation.v1'
                and admission.get('actual_admission') == 'ADMITTED' and admission.get('target_check', {}).get('result') == 'MATCH'
                and admission.get('registered_target') == b and admission.get('deployment_sha256') == after['config_sha256']
                and admission.get('target_id') == after['target_id']
                and admission.get('status_after', {}).get('helper_invocation_id') == new
                and admission.get('production_verification', {}).get('target') == b,
                'new admission observation is missing or belongs to another target/subscription')
        require(admission.get('started_at_ms', 0) >= before['completed_at_ms']
                and admission.get('completed_at_ms', float('inf')) <= after['started_at_ms'], 'admission observation must lie between snapshots')
        if after.get('runtime_variant', 'full_argus') != 'native_spire_guarded':
            require(re.fullmatch(r'[A-Za-z0-9_-]{43}', admission.get('accepted_workload_nonce') or ''),
                    'new remote-appraisal challenge association is missing')
        result.update(result='PASS', admission='OBSERVED', target=b, subscription_id=new,
                      scope='new local subscription/publication and production admission observation; Quote/EAR originals remain separate')
    except (KeyError, ValueError, TypeError) as error:
        result['reason'] = str(error)
    return result


def same_run(before, after):
    return (before.get("schema") == after.get("schema") == "argus.lifecycle-snapshot.v1" and
            all(before.get(key) and before.get(key) == after.get(key) for key in ("config_sha256", "workload_id", "target_id")) and
            type(before.get("completed_at_ms")) is int and type(after.get("started_at_ms")) is int and
            before["completed_at_ms"] <= after["started_at_ms"])


def resumed_launch(before, after, resumed, creates):
    result = {"schema": "argus.lifecycle-result.v1", "case": "resume_launch", "result": "UNKNOWN",
              "same_operation": "UNKNOWN", "duplicate_creation": "UNKNOWN", "hardware_acceptance": "NOT_RUN"}
    if not same_run(before, after):
        return result | {"reason": "snapshot association or time order differs"}
    a, b = before.get("launch", {}), after.get("launch", {})
    if not a.get("launch_id") or a.get("stage") in ("submission_unknown", "complete"):
        return result | {"reason": "before snapshot must retain a known unfinished launch"}
    if not (a["launch_id"] == b.get("launch_id") == resumed.get("launch_id") and
            b.get("stage") == "complete" and resumed.get("resumed") is True and
            a.get("config_sha256") == b.get("config_sha256") == resumed.get("config_sha256") == before["config_sha256"] and
            b.get("container_id") == resumed.get("container_id") and b.get("container_id")):
        return result | {"result": "FAIL", "same_operation": "FAIL", "reason": "resume result does not preserve the recorded operation"}
    result["same_operation"] = "PASS"
    logs = creates or []
    if (not logs or any(row.get("schema") != "argus.docker-creates.v1" or row.get("workload_id") != before["workload_id"] for row in logs) or
            [row.get("record_seq") for row in logs] != list(range(1, len(logs) + 1)) or
            logs[0].get("type") != "create_observer_start" or logs[-1].get("type") != "create_observer_stop" or
            logs[-1].get("complete") is not True or any(row.get("type") == "create_observer_error" for row in logs) or
            logs[0].get("at_ms", float("inf")) > before.get("started_at_ms", 0) or
            logs[-1].get("coverage_until_ms", 0) < after["completed_at_ms"]):
        return result | {"reason": "independent live Docker create stream lacks complete snapshot-window coverage"}
    if (logs[0].get("daemon_peer_uid") != 0 or type(logs[0].get("daemon_peer_pid")) is not int or
            logs[0].get("until_ms") != logs[-1].get("coverage_until_ms") or
            any(type(row.get("at_ms")) is not int for row in logs) or
            any(a["at_ms"] > b["at_ms"] for a, b in zip(logs, logs[1:])) or
            logs[-1]["at_ms"] < logs[-1]["coverage_until_ms"]):
        return result | {"reason": "Docker stream source/time metadata inconsistent"}
    if any(row.get("type") != "container_create" or type(row.get("created_at_ns")) is not int or
           row["created_at_ns"] <= 0 or not isinstance(row.get("container_id"), str) or
           not re.fullmatch("[0-9a-f]{64}", row["container_id"]) for row in logs[1:-1]):
        return result | {"reason": "Docker stream contains malformed or unrecognized events"}
    before_ids = {r["container_id"] for r in before.get("containers", []) if r.get("launch_id") == a["launch_id"]}
    after_ids = {r["container_id"] for r in after.get("containers", []) if r.get("launch_id") == a["launch_id"]}
    observed = [row for row in logs if row.get("type") == "container_create" and
                row.get("created_at_ns", 0) >= before["started_at_ms"] * 1000000 and
                row.get("created_at_ns", 0) <= after["completed_at_ms"] * 1000000]
    ids = [row.get("container_id") for row in observed]
    if any(row.get("launch_id") != a["launch_id"] for row in observed) or len(ids) != len(set(ids)) or (before_ids | after_ids | set(ids)) != {b["container_id"]} or after_ids != {b["container_id"]}:
        return result | {"result": "FAIL", "duplicate_creation": "FAIL", "reason": "extra or mismatched container creation during the exclusive resume experiment"}
    return result | {"result": "PASS", "duplicate_creation": "PASS", "launch_id": a["launch_id"],
                     "container_id": b["container_id"], "observed_create_events": len(ids),
                     "scope": "no second Docker create in the observed exclusive recovery window"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ('journal-anchor', 'journal-window'):
        c = sub.add_parser(name)
        c.add_argument('--config', required=True)
        c.add_argument('--output', required=True)
        if name == 'journal-window':
            c.add_argument('--anchor', required=True)
    c = sub.add_parser("quote-snapshot")
    c.add_argument("--provider-socket", required=True)
    c.add_argument("--run-id", help="required when importing this snapshot into a different host's E3 trial")
    c.add_argument("--output", required=True)
    c = sub.add_parser('time-ready-command', help='execute one explicit command and measure time to observed readiness')
    for name in ('config', 'argv-file', 'run-id', 'output'):
        c.add_argument('--'+name, required=True)
    c.add_argument('--timeout-seconds', type=float, default=120)
    c.add_argument('--poll-interval', type=float, default=.25)
    c = sub.add_parser("storage-ready")
    for name in ("config", "url", "api-key-file", "expected-content-sha256", "run-id", "output"):
        c.add_argument("--" + name, required=True)
    c = sub.add_parser("snapshot")
    c.add_argument("--config", required=True)
    c.add_argument("--output", required=True)
    c = sub.add_parser("observe-creates")
    c.add_argument("--docker-socket", default="/var/run/docker.sock")
    c.add_argument("--workload-id", required=True)
    c.add_argument("--duration", type=int, default=120)
    c.add_argument("--output", required=True)
    for name in ("rotation", "resume"):
        c = sub.add_parser(name)
        for field in ("before", "after", "output"):
            c.add_argument("--" + field, required=True)
        if name == "resume":
            c.add_argument("--resume-result", required=True)
            c.add_argument("--creates")
    args = parser.parse_args()
    if args.command in ('journal-anchor', 'journal-window'):
        atomic(args.output, journal_anchor(args.config) if args.command == 'journal-anchor' else journal_window(args.config, args.anchor))
        return
    if args.command == 'time-ready-command':
        result = time_ready_command(args.config, args.argv_file, args.run_id, args.output, args.timeout_seconds, args.poll_interval)
        print(json.dumps(result))
        raise SystemExit(0 if result['result'] == 'OBSERVED' else 1 if result['result'] == 'FAIL' else 2)
    if args.command == "quote-snapshot":
        atomic(args.output, quote_snapshot(args.provider_socket, args.run_id))
        return
    if args.command == "storage-ready":
        atomic(args.output, storage_ready(args.config, args.url, args.api_key_file, args.expected_content_sha256, args.run_id))
        return
    if args.command == "observe-creates":
        raise SystemExit(0 if observe_creates(args.docker_socket, args.workload_id, args.duration, args.output) else 2)
    if args.command == "snapshot":
        atomic(args.output, capture(args.config))
        return
    before, after = read(args.before), read(args.after)
    if args.command == "rotation":
        result = rotation(before, after)
    else:
        creates = None
        if args.creates:
            try:
                creates = [json.loads(line) for line in Path(args.creates).read_text().splitlines() if line.strip()]
            except (OSError, ValueError):
                creates = None
        result = resumed_launch(before, after, read(args.resume_result), creates)
    atomic(args.output, result)
    print(json.dumps(result))
    raise SystemExit(0 if result["result"] == "PASS" else 1 if result["result"] == "FAIL" else 2)


if __name__ == "__main__":
    main()
