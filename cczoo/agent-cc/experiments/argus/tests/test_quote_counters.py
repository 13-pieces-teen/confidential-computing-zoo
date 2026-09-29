"""Source-bound counter arithmetic; test definitions do not prove remote TDX."""
import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lifecycle_evidence import quote_delta


def pair():
    first = {"schema": "argus.quote-counters.v1", "provider_instance_id": "boot:123:1", "agent_id": "agent",
             "socket_path": "/run/provider.sock", "observation_started_at_ms": 1000, "observation_completed_at_ms": 1010,
             "node": {"attempted": 1, "generated": 1, "failed": 0}, "workload": {"attempted": 2, "generated": 1, "failed": 1}}
    second = copy.deepcopy(first)
    second.update(observation_started_at_ms=2000, observation_completed_at_ms=2010)
    return first, second


def test_rotation_zero_and_three_discarded_quotes_are_distinct():
    before, after = pair()
    assert quote_delta(before, after)["workload"]["generated"] == 0
    after["workload"].update(attempted=5, generated=4)
    assert quote_delta(before, after)["workload"]["generated"] == 3
    assert quote_delta(before, after)["node"]["generated"] == 0


def test_restart_inflight_missing_and_counter_reset_are_unknown():
    for change in (lambda r: r.update(provider_instance_id="restarted"),
                   lambda r: r["workload"].update(attempted=3),
                   lambda r: r["node"].pop("generated"),
                   lambda r: r["node"].update(attempted=0, generated=0)):
        before, after = pair()
        change(after)
        assert quote_delta(before, after)["result"] == "UNKNOWN"


def test_generation_timing_is_separate_from_counts_and_includes_failures():
    before, after = pair()
    before['workload']['generation_elapsed_ns'] = 20_000_000
    after['workload'].update(attempted=5, generated=3, failed=2, generation_elapsed_ns=80_000_000)
    result = quote_delta(before, after)
    assert result['workload'] == {'attempted': 3, 'generated': 2, 'failed': 1}
    timing = result['generation_timing']['workload']
    assert timing['completed_calls'] == 3 and timing['elapsed_ns'] == 60_000_000 and timing['mean_ms'] == 20
    assert result['generation_timing']['node']['result'] == 'UNKNOWN'
    assert result['result'] == 'OBSERVED'  # Older Providers still supply valid counts.
