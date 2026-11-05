import tempfile,unittest
from pathlib import Path
from qc_shadow.control_evidence import record_snapshot
class EvidenceTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def snapshot(self):
  return {'account_id':'a','as_of':'2026-11-04T10:00:00+08:00','plans':[{'plan_id':'p','scope':'chuanfang_goods','first_enabled_on':'2026-11-01','first_enabled_evidence':'proof','daily':[{'day':'2026-11-01','complete':True,'daily_aggregate_matches':True,'spend':'1','gmv':'3','orders':1,'evidence':'x'}],'refund':{'verified':False,'band':'gt300'},'budget':{'amount':'5000'},'observations':[]} ]}
 def test_missing_days_explicit_and_B_not_promoted(self):
  p=record_snapshot(self.home,self.snapshot())['plans'][0]
  self.assertEqual(p['missing_daily_dates'],['2026-11-02','2026-11-03']);self.assertIsNone(p['formal_B']);self.assertFalse(p['refund']['verified']);self.assertFalse(p['execution_allowed'])
 def test_unknown_first_is_not_creation_or_first_seen(self):
  s=self.snapshot();s['plans'][0]['first_enabled_on']=None
  p=record_snapshot(self.home,s)['plans'][0];self.assertIsNone(p['missing_daily_dates']);self.assertIn('first_enabled_unknown',p['blockers'])
 def test_old_nodes_do_not_create_pause_or_pass(self):
  s=self.snapshot();s['plans'][0]['first_enabled_on']='2026-09-01'
  p=record_snapshot(self.home,s)['plans'][0];self.assertEqual(p['historical_policy'],'replay_only_current_decisions');self.assertEqual(p['lifecycle_stage'],'unverified');self.assertFalse(p['plan_pause_enabled'])
 def test_out_of_scope_omitted(self):
  s=self.snapshot();s['plans'][0]['scope']='unverified';self.assertEqual(record_snapshot(self.home,s)['plans'],[])
