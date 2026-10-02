"""Fixed, local E2 liveness observations; never an admission or memory probe.

The trusted observer enters only the registered process's network namespace.
It sends an unauthenticated GET /health to loopback with no private input. No
listener, forwarding rule, application route, or reusable bypass is installed.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from common import require


# The command is constant code, not an operator supplied shell/URL. Its output
# contains status only; arbitrary service response text never enters evidence.
HEALTH_CODE = """import http.client,json,sys
c=http.client.HTTPConnection('127.0.0.1',int(sys.argv[1]),timeout=1)
try:
 c.request('GET','/health',headers={'Connection':'close'})
 r=c.getresponse(); b=r.read(65537)
 ok=False
 if len(b)<=65536:
  try:
   v=json.loads(b); ok=isinstance(v,dict) and (v.get('status')=='ok' or v.get('healthy') is True) and v.get('healthy',True) is not False
  except (ValueError,TypeError): pass
 print(json.dumps({'http_status':r.status,'health_ok':r.status==200 and ok}))
finally: c.close()
"""


def process_binding(target, proc=Path('/proc')):
    """Observe original PID/start/boot/netns and its listening socket ownership.

    Deliberately does not recheck the approved configuration digest: E2 may have
    invalidated that digest while the original application is still healthy.
    """
    pid, port = str(target.get('pid', '')), str(target.get('listen_port', ''))
    require(re.fullmatch(r'[1-9][0-9]*', pid) and re.fullmatch(r'[1-9][0-9]*', port)
            and int(port) <= 65535, 'canonical registered PID/port required')
    root = proc / pid
    stat = (root / 'stat').read_text()
    fields = stat[stat.rindex(')') + 2:].split()
    observed = {'pid': pid, 'start_time': fields[19],
                'boot_id': (proc / 'sys/kernel/random/boot_id').read_text().strip(),
                'net_namespace': os.readlink(root / 'ns/net')}
    require(all(observed[key] == str(target.get(key, '')) for key in observed),
            'original backend process identity changed')
    sockets = set()
    for fd in (root / 'fd').iterdir():
        try:
            link = os.readlink(fd)
        except FileNotFoundError:
            continue
        matched = re.fullmatch(r'socket:\[([0-9]+)\]', link)
        if matched:
            sockets.add(matched[1])
    listeners = set()
    for name in ('tcp', 'tcp6'):
        path = root / 'net' / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines()[1:]:
            values = line.split()
            if len(values) >= 10 and values[3] == '0A' and int(values[1].rsplit(':', 1)[1], 16) == int(port) and values[9] in sockets:
                listeners.add(values[9])
    require(listeners, 'original backend does not own the registered listener')
    return dict(observed, listen_port=port, listener_inodes=sorted(listeners))


def sample(target, *, inspect=process_binding, execute=subprocess.run):
    started = time.time_ns() // 1_000_000
    result = {'schema': 'argus.backend-health.v1', 'result': 'UNKNOWN',
              'started_at_ms': started, 'target': target,
              'scope': 'sampled original-process GET /health; not memory correctness or admission',
              'route': 'loopback_in_registered_netns', 'private_input_bytes': 0}
    try:
        require(sys.platform == 'linux' and os.geteuid() == 0, 'trusted Linux root observer required')
        before = inspect(target)
        completed = execute(['nsenter', '--target', str(target['pid']), '--net', '--',
                             sys.executable, '-I', '-c', HEALTH_CODE, str(target['listen_port'])],
                            capture_output=True, timeout=3, check=False)
        after = inspect(target)
        require(before == after, 'backend identity/listener changed during the probe')
        require(completed.returncode == 0 and len(completed.stdout) <= 1024, 'local health request did not complete')
        response = json.loads(completed.stdout)
        require(type(response.get('http_status')) is int and type(response.get('health_ok')) is bool,
                'invalid fixed probe output')
        result.update(process=before, http_status=response['http_status'], health_ok=response['health_ok'],
                      result='OBSERVED' if response['health_ok'] else 'UNKNOWN')
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError) as error:
        result['error_class'] = type(error).__name__
    result['completed_at_ms'] = time.time_ns() // 1_000_000
    return result
