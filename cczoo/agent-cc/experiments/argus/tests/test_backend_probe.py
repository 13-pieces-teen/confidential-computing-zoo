"""Trusted-local liveness fixtures, not remote TDX or memory availability."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend_probe
import timeline


def target():
    return dict(pid='42', start_time='100', boot_id='boot', net_namespace='net:[10]', listen_port='1933',
                container_id='a'*64, launch_id='launch')


def test_probe_sends_only_fixed_loopback_get_and_keeps_response_private(monkeypatch):
    monkeypatch.setattr(backend_probe.sys, 'platform', 'linux')
    monkeypatch.setattr(backend_probe.os, 'geteuid', lambda: 0, raising=False)
    calls = []
    def invoke(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'{"http_status":200,"health_ok":true}')
    binding = dict(target(), listener_inodes=['3'])
    result = backend_probe.sample(target(), inspect=lambda _: binding, execute=invoke)
    assert result['result'] == 'OBSERVED' and result['private_input_bytes'] == 0
    assert calls[0][:5] == ['nsenter', '--target', '42', '--net', '--']
    assert calls[0][-1] == '1933'
    assert "'127.0.0.1'" in calls[0][-2] and "'GET','/health'" in calls[0][-2]
    assert 'Authorization' not in calls[0][-2] and 'X-API-Key' not in calls[0][-2]
    assert 'response_body' not in result


@pytest.mark.parametrize('failure', ['changed', 'dead', 'not_ok', 'timeout'])
def test_failed_or_replaced_backend_is_unknown_not_protection_success(monkeypatch, failure):
    monkeypatch.setattr(backend_probe.sys, 'platform', 'linux')
    monkeypatch.setattr(backend_probe.os, 'geteuid', lambda: 0, raising=False)
    observed = 0
    def inspect(_):
        nonlocal observed
        observed += 1
        if failure == 'dead': raise FileNotFoundError()
        return dict(target(), start_time='101' if failure == 'changed' and observed == 2 else '100')
    def execute(*args, **kwargs):
        if failure == 'timeout': raise backend_probe.subprocess.TimeoutExpired(args[0], 3)
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(http_status=200, health_ok=failure != 'not_ok')).encode())
    assert backend_probe.sample(target(), inspect=inspect, execute=execute)['result'] == 'UNKNOWN'


def test_absence_and_unassociated_liveness_never_become_live_after_close():
    fault = dict(run_id='run', target=target(), started_at_ms=1000, completed_at_ms=1010)
    stop = dict(status='OBSERVED', lower_ms=50, upper_ms=100)
    assert timeline.backend_availability(fault, [], stop, 0)['result'] == 'UNKNOWN'
    probe = dict(schema='argus.backend-health.v1', result='OBSERVED', started_at_ms=1200, completed_at_ms=1210,
                 target=target(), process=dict(target(), listener_inodes=['3']), private_input_bytes=0,
                 route='loopback_in_registered_netns', http_status=200, health_ok=True)
    rows = [dict(run_id='run', backend_probe=probe)]
    result = timeline.backend_availability(fault, rows, stop, 0)
    assert result['after_fault_samples'] == result['after_entry_stop_samples'] == 1
    assert result['live_after_entry_stop'] == 'OBSERVED'
    bad = copy.deepcopy(rows); bad[0]['backend_probe']['target']['start_time'] = '101'
    assert timeline.backend_availability(fault, bad, stop, 0)['live_after_entry_stop'] == 'UNKNOWN'
    assert timeline.backend_availability(fault, rows, stop, 200)['live_after_entry_stop'] == 'UNKNOWN'


@pytest.mark.parametrize('status', [200, 302, 500])
def test_fixed_http_probe_rejects_redirect_and_does_not_emit_payload(status):
    # Execute the exact request code against a real local HTTP fixture; nsenter
    # and original process binding are separately tested above/on Linux hosts.
    import http.server
    import threading
    import subprocess
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.path == '/health' and self.command == 'GET'
            body = b'{"status":"ok","private":"do-not-export"}'
            self.send_response(status); self.send_header('Location','http://example.invalid/private'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        result = subprocess.run([sys.executable, '-I', '-c', backend_probe.HEALTH_CODE, str(server.server_port)], capture_output=True, timeout=3, check=True)
        assert json.loads(result.stdout) == dict(http_status=status, health_ok=status == 200)
        assert b'do-not-export' not in result.stdout
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_process_binding_requires_original_pid_start_and_owned_listener(tmp_path, monkeypatch):
    proc=tmp_path/'proc'; root=proc/'42'
    for path in (root/'ns',root/'fd',root/'net',proc/'sys/kernel/random'):
        path.mkdir(parents=True)
    (proc/'sys/kernel/random/boot_id').write_text('boot')
    fields=['S']+['0']*18+['100']
    (root/'stat').write_text('42 (name with spaces) '+' '.join(fields))
    (root/'ns/net').write_text('net:[10]'); (root/'fd/3').write_text('socket:[123]')
    (root/'net/tcp').write_text('header\n0: 0100007F:078D 00000000:0000 0A 0 0 0 0 0 123\n')
    monkeypatch.setattr(backend_probe.os,'readlink',lambda path: Path(path).read_text())
    assert backend_probe.process_binding(target(),proc)['listener_inodes']==['123']
    changed=target();changed['start_time']='101'
    with pytest.raises(ValueError,match='identity changed'):backend_probe.process_binding(changed,proc)
    (root/'fd/3').write_text('socket:[124]')
    with pytest.raises(ValueError,match='listener'):backend_probe.process_binding(target(),proc)


def test_opted_in_backend_probe_must_pass_before_fault_baseline(tmp_path, monkeypatch):
    from unittest.mock import patch
    state=dict(ready=True,entry_active=True,entry_stopped=False,helper_active=True)
    with patch.object(timeline.time,'monotonic',side_effect=[0,0,.1,2]),patch.object(timeline.time,'sleep'):
        timeline.observe('fixture',tmp_path/'events.jsonl','run',duration=1,sample=lambda _: dict(state),
                         backend_target=target(),backend_sample=lambda _: {'result':'UNKNOWN'})
    rows=[json.loads(line) for line in (tmp_path/'events.jsonl').read_text().splitlines()]
    assert not any(r['type']=='observer_ready' for r in rows)
    assert rows[-1]['healthy_baseline'] is False
