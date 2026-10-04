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

if __name__=='__main__':unittest.main()
