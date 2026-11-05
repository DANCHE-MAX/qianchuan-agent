import unittest,tempfile,copy
from datetime import date,timedelta
from qc_shadow.collection_scope import ACCOUNT,decision_start,scoped_lifecycle
from qc_shadow.control_evidence import record_snapshot
from test_preflight import PreflightTests
from qc_shadow.preflight import build_preflight
class ScopeTests(unittest.TestCase):
 def fixture(self):
  s,l,a=PreflightTests().fixture()
  for v in (s,l,a):v['account_id']=ACCOUNT
  p=s['plans'][0];p['first_enabled_on']=None;p['first_enabled_evidence']=None
  p['daily']=[dict(p['daily'][0],day=str(date(2026,10,22)+timedelta(days=i))) for i in range(13)]
  return s,l,a
 def test_full_window_without_original_start(self):
  s,l,a=self.fixture()
  r=build_preflight(s,l,a,'2026-11-04T10:00:11+08:00')
  self.assertTrue(all(r['checks'].values()),r)
  self.assertIsNone(s['plans'][0]['first_enabled_on'])
  with tempfile.TemporaryDirectory() as h:
   fact=record_snapshot(h,s)['plans'][0]
   self.assertEqual(fact['missing_daily_dates'],[])
   self.assertEqual(fact['completeness_start'],'2026-10-22')
 def test_missing_or_unreconciled_day_still_blocks(self):
  for mode in ('missing','unreconciled'):
   s,l,a=self.fixture()
   if mode=='missing':s['plans'][0]['daily'].pop(0)
   else:s['plans'][0]['daily'][0]['daily_aggregate_matches']=False
   r=build_preflight(s,l,a,'2026-11-04T10:00:11+08:00')
   self.assertFalse(all(r['checks'].values()))
 def test_old_lifecycle_nodes_not_restarted(self):
  s,l,a=self.fixture();s['plans'][0]['first_enabled_on']='2026-07-15'
  r=build_preflight(s,l,a,'2026-11-04T10:00:11+08:00')
  self.assertTrue(r['checks']['lifecycle_verified'])
  self.assertTrue(all(e['rule']=='outside_authorized_window' for e in r['lifecycle']['events']))
 def test_other_account_and_new_plan(self):
  self.assertEqual(decision_start('other','2026-07-15'),'2026-07-15')
  self.assertEqual(decision_start(ACCOUNT,'2026-11-01'),'2026-11-01')
