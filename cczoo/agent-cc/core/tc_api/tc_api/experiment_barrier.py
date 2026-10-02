"""Opt-in, root-only lifecycle fault barriers. Never used to release a fence.

The operator arms one point in an isolated experiment and can release it or kill
the held process. Timeout fails the operation; it never silently continues.
Receipts are observations, not a durable measurement-recovery journal.
"""
import json
import os
from pathlib import Path
import re
import stat
import time
import tempfile

POINTS = frozenset({'after_reserve', 'after_forward', 'after_result', 'after_sign',
                    'before_confirm', 'after_rtmr_extend'})


def protected(path, directory=False):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('barrier path must be absolute and not use symlinks')
    for entry in (path, *path.parents):
        info = entry.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('barrier path must be root-owned without group/world write')
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('invalid barrier file type')
    if info.st_mode & 0o077:
        raise ValueError('barrier data must be accessible only to root')
    return path


def validate(value):
    if (set(value) != {'schema', 'barrier_id', 'point', 'operation_type', 'mutation_id', 'timeout_seconds'}
            or value['schema'] != 'argus.lifecycle-barrier.v1'
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value['barrier_id'])
            or value['point'] not in POINTS
            or value['operation_type'] not in {'create', 'start', 'stop', 'rm'}
            or (value['mutation_id'] is not None and not re.fullmatch(r'mutation-[0-9a-f]{32}', value['mutation_id']))
            or type(value['timeout_seconds']) is not int or not 1 <= value['timeout_seconds'] <= 3600):
        raise ValueError('invalid lifecycle barrier configuration')
    return value


def _exclusive(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.barrier-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic, create-only publication: readers never see a partial release
        # and a second operation cannot replace the first operation's receipt.
        os.link(temporary, path)
        if hasattr(os, 'O_DIRECTORY'):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        os.unlink(temporary)


def checkpoint(point, *, mutation_id, operation_type=None, record_id=None):
    directory = os.environ.get('ARGUS_EXPERIMENT_BARRIER_DIR')
    if not directory:
        return
    if os.environ.get('ARGUS_EXPERIMENT_MODE') != '1' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise RuntimeError('lifecycle barriers require explicit experiment mode and Linux root')
    directory = protected(directory, directory=True)
    if not directory.is_relative_to('/srv/argus-experiments'):
        raise ValueError('barrier directory must stay below /srv/argus-experiments')
    config_file = directory / 'armed.json'
    if not config_file.exists():
        return
    config = validate(json.loads(protected(config_file).read_text(encoding='utf-8')))
    if config['point'] != point or config['mutation_id'] not in (None, mutation_id):
        return
    if config['operation_type'] != operation_type:
        return
    if not re.fullmatch(r'mutation-[0-9a-f]{32}', mutation_id or ''):
        raise ValueError('barrier requires a durable mutation ID')
    reached = directory / (config['barrier_id'] + '.reached.json')
    receipt = {'schema': 'argus.lifecycle-barrier-receipt.v1', **{k: config[k] for k in ('barrier_id', 'point')},
               'mutation_id': mutation_id, 'operation_type': operation_type, 'record_id': record_id,
               'pid': os.getpid(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
               'reached_at_ms': time.time_ns() // 1_000_000, 'reached_monotonic_ns': time.monotonic_ns(),
               'classification': 'MEASUREMENT_COMMIT_UNKNOWN' if point == 'after_rtmr_extend' else 'HELD',
               'scope': 'experiment observation; not automatic outcome or measurement reconciliation'}
    try:
        _exclusive(reached, receipt)
    except FileExistsError:
        previous = json.loads(protected(reached).read_text(encoding='utf-8'))
        if any(previous.get(k) != receipt[k] for k in ('barrier_id', 'point', 'mutation_id', 'operation_type')):
            # One barrier may hold exactly one operation, never unrelated work.
            return
    deadline = time.monotonic() + config['timeout_seconds']
    released = directory / (config['barrier_id'] + '.release.json')
    while time.monotonic() < deadline:
        if released.exists():
            value = json.loads(protected(released).read_text(encoding='utf-8'))
            if value != {'barrier_id': config['barrier_id'], 'mutation_id': mutation_id, 'action': 'continue'}:
                raise ValueError('barrier release does not match the held mutation')
            return
        time.sleep(0.05)
    raise TimeoutError('experiment barrier timed out; lifecycle result remains unresolved')
