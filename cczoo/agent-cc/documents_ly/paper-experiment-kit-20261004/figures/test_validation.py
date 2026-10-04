"""Local checks use isolated copies, never populate the delivered template."""
import copy,json,unittest
from pathlib import Path
from validate_results import validate
BASE=json.loads((Path(__file__).with_name('results.json')).read_text(encoding='utf-8'))

class EvidenceTests(unittest.TestCase):
    def test_empty_template_is_not_a_result(self):
        self.assertEqual(validate(BASE),[])
        self.assertTrue(all(r['status']=='pending' for k in ('admission','history','receiver','tasks','cost','reuse') for r in BASE[k]))
    def test_absence_cannot_become_zero_receipt(self):
        d=copy.deepcopy(BASE);r=d['receiver'][0]
        r.update(status='unknown',run_id='TEST',evidence_ref='isolated test',coverage='GAP',new_facts_read=0)
        self.assertTrue(any('zero receipt requires' in x for x in validate(d)))
    def test_failed_slot_cannot_disappear(self):
        d=copy.deepcopy(BASE);d['tasks'].pop()
        self.assertTrue(any('every planned slot' in x for x in validate(d)))
    def test_duplicate_run_cannot_increase_n(self):
        d=copy.deepcopy(BASE)
        for r in d['tasks'][:2]:r.update(status='unknown',run_id='SAME_TEST',evidence_ref='isolated test')
        self.assertTrue(any('one actual run' in x for x in validate(d)))
    def test_task_pass_requires_all_six_steps(self):
        d=copy.deepcopy(BASE);r=d['tasks'][0]
        r.update(status='recorded',run_id='TEST',evidence_ref='isolated test',complete_task='PASS')
        self.assertTrue(any('PASS cannot hide' in x for x in validate(d)))
    def test_latency_cannot_hide_request_outcomes(self):
        d=copy.deepcopy(BASE);r=d['cost'][0]
        r.update(status='recorded',run_id='TEST',evidence_ref='isolated test',p50_ms=1,p95_ms=2,
                 attempted=10,valid=8,rejected=0,failed=0,timed_out=0,invalid=0,duration_s=120)
        self.assertTrue(any('partition attempted' in x for x in validate(d)))
    def test_unclosed_is_censored_not_zero_seconds(self):
        d=copy.deepcopy(BASE);r=d['receiver'][0]
        r.update(status='recorded',run_id='TEST',evidence_ref='isolated test',closure_observation='RIGHT_CENSORED',closed_s=0,window_end_s=10)
        self.assertTrue(any('censored closure' in x for x in validate(d)))

    def test_recorded_controlled_sample_is_accepted(self):
        d=copy.deepcopy(BASE);r=d['admission'][0]
        r.update(status='recorded',run_id='TEST',evidence_ref='isolated test',
                 environment_id='TEST-LINUX',runtime_revision='TEST-REV',
                 decision='ALLOW')
        self.assertEqual(validate(d),[])

    def test_environment_cannot_be_omitted(self):
        d=copy.deepcopy(BASE);r=d['admission'][0]
        r.update(status='recorded',run_id='TEST',evidence_ref='isolated test',decision='ALLOW')
        self.assertTrue(any('environment_id and runtime_revision' in x for x in validate(d)))

    def test_tdx_archive_cannot_enter_controlled_statistics(self):
        d=copy.deepcopy(BASE);r=d['admission'][0]
        r.update(status='recorded',run_id='TEST',evidence_ref='isolated test',
                 environment_id='TDX-ARCHIVE',runtime_revision='TEST-REV',
                 evidence_kind='real_tdx_archive',decision='ALLOW')
        self.assertTrue(any('evidence kind mismatch' in x for x in validate(d)))

    def test_software_requests_cannot_be_called_hardware_quotes(self):
        d=copy.deepcopy(BASE);d['reuse'][0]['node_quotes']=None
        self.assertTrue(any('hardware Quote counts' in x for x in validate(d)))

    def test_software_stage_cannot_be_called_quote_timing(self):
        d=copy.deepcopy(BASE)
        d['stage_costs'].append(dict(status='recorded',run_id='TEST',evidence_ref='isolated test',
            environment_id='TEST-LINUX',runtime_revision='TEST-REV',evidence_kind='controlled_prototype',
            arm='full',phase='admission',stage='quote',duration_ms=1))
        self.assertTrue(any('not hardware Quote timing' in x for x in validate(d)))

    def test_offline_history_remains_a_separate_evidence_kind(self):
        d=copy.deepcopy(BASE);r=d['history'][0]
        r.update(status='recorded',run_id='TEST',evidence_ref='isolated test',
                 environment_id='TEST-OFFLINE',runtime_revision='TEST-REV',
                 source_kind='signed_fixture',verdict='ALLOW')
        self.assertEqual(validate(d),[])
        r['evidence_kind']='controlled_prototype'
        self.assertTrue(any('evidence kind mismatch' in x for x in validate(d)))

if __name__=='__main__':unittest.main()
