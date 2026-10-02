import pytest
import json
import threading
import time
from pathlib import Path
from tc_api import experiment_barrier as barrier


def test_disabled_barrier_has_no_io(monkeypatch):
    monkeypatch.delenv('ARGUS_EXPERIMENT_BARRIER_DIR', raising=False)
    monkeypatch.setattr(barrier, 'protected', lambda *_args, **_kwargs: pytest.fail('disabled barrier read disk'))
    barrier.checkpoint('after_reserve', mutation_id='unused')


def test_environment_path_alone_cannot_enable_faults(monkeypatch):
    monkeypatch.setenv('ARGUS_EXPERIMENT_BARRIER_DIR', '/srv/argus-experiments/test')
    monkeypatch.delenv('ARGUS_EXPERIMENT_MODE', raising=False)
    with pytest.raises(RuntimeError, match='explicit experiment mode'):
        barrier.checkpoint('after_reserve', mutation_id='mutation-' + 'a' * 32)


def test_barrier_configuration_is_bounded_and_has_no_command_or_reset():
    value = dict(schema='argus.lifecycle-barrier.v1', barrier_id='test', point='before_confirm',
                 operation_type='start', mutation_id=None, timeout_seconds=10)
    assert barrier.validate(value) == value
    for patch in ({'point': 'force-confirm'}, {'operation_type': 'exec'}, {'timeout_seconds': 0},
                  {'timeout_seconds': 3601}, {'mutation_id': '../../escape'}, {'command': 'anything'}):
        with pytest.raises(ValueError):
            barrier.validate({**value, **patch})


def test_checkpoint_waits_for_exact_mutation_release_and_records_no_command(tmp_path, monkeypatch):
    # Windows unit test exercises the waiting/file contract, not Linux ownership.
    directory = tmp_path.resolve()
    monkeypatch.setenv('ARGUS_EXPERIMENT_BARRIER_DIR', str(directory))
    monkeypatch.setenv('ARGUS_EXPERIMENT_MODE', '1')
    monkeypatch.setattr(barrier.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(barrier, 'protected', lambda path, **kw: Path(path))
    monkeypatch.setattr(Path, 'is_relative_to', lambda self, other: self == directory)
    read_text = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **kw: 'boot-test' if self.as_posix().endswith('random/boot_id') else read_text(self, *a, **kw))
    config = {'schema': 'argus.lifecycle-barrier.v1', 'barrier_id': 'cut', 'point': 'after_result',
              'operation_type': 'start', 'mutation_id': None, 'timeout_seconds': 5}
    barrier._exclusive(directory / 'armed.json', config)
    errors, finished = [], threading.Event()
    mutation = 'mutation-' + 'a' * 32
    def run():
        try:
            barrier.checkpoint('after_result', mutation_id=mutation, operation_type='start')
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    thread = threading.Thread(target=run)
    thread.start()
    try:
        reached = directory / 'cut.reached.json'
        deadline = time.monotonic() + 2
        while not reached.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert reached.exists() and not finished.is_set()
        receipt = json.loads(read_text(reached))
        assert receipt['mutation_id'] == mutation and receipt['point'] == 'after_result'
        assert 'command' not in receipt
        barrier._exclusive(directory / 'cut.release.json', {'barrier_id': 'cut', 'mutation_id': mutation, 'action': 'continue'})
        assert finished.wait(2) and not errors
    finally:
        thread.join(timeout=6)
