"""Scope reduction must preserve positive observations and localize gaps."""
import unittest

import test_remote_acceptance as previous
remote = previous.remote


class ReceiverWindowTests(unittest.TestCase):
    def setUp(self):
        fixture = previous.TrafficEvidenceTests(); fixture.setUp()
        self.rows, self.event = fixture.rows, fixture.event
        self.process = {'pid':123, 'start_time':'42'}
        self.binding = {'instance_id':'a'*64, 'launch_id':'launch', 'process':self.process}
        self.logs = [dict(type='receiver_start', binding=self.binding, at_ms=0),
                     dict(type='source_seen', source_id='source', process=self.process, at_ms=0)]
        for sample in self.rows:
            if sample.get('ok'):
                self.logs.append(dict(type='received', phase='body_read', source_id='source', process=self.process,
                         request_id=sample['request_id'], at_ms=sample['started_at_ms'], received_body_bytes=12,
                         message_type='http.request', provenance='kernel_process_and_deployment'))
        self.logs += [dict(type='coverage_interval', status='COMPLETE', source_id='source', started_at_ms=0, ended_at_ms=10000),
                      dict(type='receiver_stop', at_ms=10000)]
        for i, row in enumerate(self.logs, 1):
            row.update(schema_version=2, record_seq=i, collector_id='collector', run_id='run',
                       instance_id='a'*64, launch_id='launch')

    def check(self):
        for i, row in enumerate(self.logs, 1):
            if row.get('schema_version') == 2:
                row['record_seq'] = i
        return remote.assess(self.rows, self.event, self.logs, bound_ms=1000, clock_uncertainty_ms=0)

    def read(self, at, request_id='existing3', **changes):
        row = dict(self.logs[2], request_id=request_id, at_ms=at, received_body_bytes=19)
        row.update(changes); self.logs.append(row)

    def test_inflight_body_started_before_fault_is_still_a_violation(self):
        self.read(6500)
        result = self.check()
        self.assertEqual(result['receiver_delivery'], 'FAIL')
        self.assertEqual(result['received_body_bytes'], 19)

    def test_known_prebound_gap_does_not_poison_later_coverage(self):
        self.logs.append(dict(self.logs[-2], status='UNKNOWN', started_at_ms=1000, ended_at_ms=2000))
        self.assertEqual(self.check()['receiver_delivery'], 'PASS')
        self.logs[-1].update(started_at_ms=6500, ended_at_ms=7000)
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')
        self.read(7500)
        self.assertEqual(self.check()['receiver_delivery'], 'FAIL')

    def test_source_crash_tail_and_missing_baseline_are_unknown(self):
        self.logs[-2]['ended_at_ms'] = 6500
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')
        self.logs[-2]['ended_at_ms'] = 10000
        self.logs.pop(2)
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')

    def test_replacement_is_observed_without_being_granted_admission(self):
        replacement = dict(self.logs[1], source_id='replacement', process={'pid':321, 'start_time':'73'})
        self.logs.append(replacement)
        self.read(7000, source_id='replacement', process=replacement['process'])
        self.assertEqual(self.check()['receiver_delivery'], 'FAIL')
        self.logs[-1]['provenance'] = 'client_claim'
        self.assertNotEqual(self.check()['receiver_delivery'], 'FAIL')

    def test_new_container_needs_explicit_observation_binding(self):
        new_binding = {'instance_id':'b'*64, 'launch_id':'second', 'process':{'pid':321, 'start_time':'73'}}
        self.logs.append(dict(self.logs[1], instance_id='b'*64, launch_id='second', source_id='replacement', process=new_binding['process']))
        self.read(7000, instance_id='b'*64, launch_id='second', source_id='replacement', process=new_binding['process'])
        self.assertNotEqual(self.check()['receiver_delivery'], 'FAIL')
        self.logs.append(dict(self.logs[0], type='target_added', binding=new_binding))
        self.assertEqual(self.check()['receiver_delivery'], 'FAIL')

    def test_requested_stream_must_be_observed_before_fault(self):
        self.rows[0]['inflight_required'] = True
        self.rows.append({'type':'stream_start', 'run_id':'run', 'lane':'inflight', 'request_id':'slow', 'started_at_ms':2000})
        self.assertEqual(self.check()['inflight_delivery'], 'UNKNOWN')
        self.read(3000, request_id='slow')
        self.assertEqual(self.check()['inflight_delivery'], 'PASS')

    def test_unreadable_tail_never_creates_zero_receipt_but_keeps_positive(self):
        self.logs.append({'type':'receiver_gap', 'run_id':'run', 'code':'unreadable_or_incomplete_file'})
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')
        self.read(7000)
        self.assertEqual(self.check()['receiver_delivery'], 'FAIL')

    def test_same_host_monotonic_timestamp_avoids_wall_clock_step(self):
        self.event['clock_id'] = 'boot:abc'
        self.process['boot_id'] = 'abc'
        self.event['started_monotonic_ns'] = 10_000_000_000
        self.read(100, monotonic_ns=12_000_000_000)
        self.assertEqual(self.check()['receiver_delivery'], 'FAIL')

    def test_other_boot_monotonic_counter_cannot_hide_post_bound_receipt(self):
        self.event.update(clock_id='boot:abc', started_monotonic_ns=100_000_000_000)
        self.process['boot_id'] = 'def'
        self.read(7000, monotonic_ns=1_000_000_000)
        self.assertEqual(self.check()['receiver_delivery'], 'FAIL')

    def test_unknown_clock_uses_wall_window_instead_of_unrelated_uptime(self):
        self.event['started_monotonic_ns'] = 100_000_000_000
        self.read(1000, monotonic_ns=120_000_000_000)
        self.assertEqual(self.check()['receiver_delivery'], 'PASS')

    def test_clock_uncertainty_cannot_turn_possible_post_bound_read_into_pass(self):
        self.read(6050)
        for i, row in enumerate(self.logs, 1):
            row['record_seq'] = i
        result = remote.assess(self.rows, self.event, self.logs, bound_ms=1000,
                               clock_uncertainty_ms=100, min_samples=2)
        self.assertEqual(result['receiver_delivery'], 'UNKNOWN')

    def test_delayed_collector_receipt_cannot_shorten_source_coverage(self):
        self.logs[1]['at_ms'] = 12000
        self.logs[-2]['status'] = 'UNKNOWN'
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')
        self.logs[-2]['status'] = 'COMPLETE'
        self.assertEqual(self.check()['receiver_delivery'], 'PASS')
        self.logs[-2].update(started_at_ms=12000, ended_at_ms=13000)
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')

    def test_unbound_peer_after_bound_prevents_absence_claim(self):
        self.logs.append(dict(self.logs[-1], type='receiver_gap', reason='unbound_process', at_ms=7000))
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')
        self.logs[-1]['at_ms'] = 1000
        self.assertEqual(self.check()['receiver_delivery'], 'PASS')

    def test_successor_coverage_cannot_hide_original_source_gap(self):
        self.logs[-2]['started_at_ms'] = 7000
        self.logs.append(dict(self.logs[1], source_id='replacement', process={'pid':321, 'start_time':'73'}))
        self.logs.append(dict(self.logs[-3], source_id='replacement', started_at_ms=0))
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')

    def test_persisted_sequence_hole_and_monotonic_gap_cannot_pass(self):
        self.logs[-2]['record_seq'] += 10
        self.assertEqual(remote.assess(self.rows, self.event, self.logs, bound_ms=1000,
                         clock_uncertainty_ms=0)['receiver_delivery'], 'UNKNOWN')
        self.event['started_monotonic_ns'] = 10_000_000_000
        self.event['clock_id'] = 'boot:abc'
        self.process['boot_id'] = 'abc'
        for row in self.logs:
            row['monotonic_ns'] = (row.get('at_ms', 0) + 5000) * 1_000_000
        self.logs[-2].update(started_monotonic_ns=5_000_000_000, ended_monotonic_ns=15_000_000_000)
        self.logs.append(dict(self.logs[-2], status='UNKNOWN', started_at_ms=1000, ended_at_ms=2000,
                             started_monotonic_ns=12_000_000_000, ended_monotonic_ns=13_000_000_000))
        self.assertEqual(self.check()['receiver_delivery'], 'UNKNOWN')


if __name__ == '__main__': unittest.main()
