import unittest
from qc_shadow.gap_recovery import requested_gaps,classify
class RecoveryTests(unittest.TestCase):
 def test_unknown_first_only_recent_dates(self):
  g=requested_gaps([{'plan_id':'2','daily':[]}],'2026-11-04T12:00:00+08:00');self.assertEqual(len(g['2']),7)
 def test_empty_success_not_zero(self):
  r=classify({'2':['2026-11-03']},[],{'2026-11-03':True},'proof');self.assertEqual(r['2'][0]['status'],'not_returned');self.assertNotIn('spend',r['2'][0])
 def test_no_reconciliation_no_recovered_value(self):
  row={'plan_id':'2','day':'2026-11-03','spend':'1','gmv':'2','orders':1}
  r=classify({'2':['2026-11-03']},[row],{'2026-11-03':False},'proof');self.assertEqual(r['2'][0]['status'],'unreconciled')
 def test_verified_row_keeps_evidence_and_no_budget_clearance(self):
  row={'plan_id':'2','day':'2026-11-03','spend':'1','gmv':'2','orders':1};r=classify({'2':['2026-11-03']},[row],{'2026-11-03':True},'proof')['2'][0]
  self.assertEqual(r['status'],'recovered_shadow_only');self.assertFalse(r['budget_clearance']);self.assertEqual(r['row']['evidence'],'proof')
 def test_transient_retry_bound_and_nontransient_block(self):
  from qc_shadow.gap_recovery import retry_allowed
  from datetime import datetime
  old={'status':'failed','at':'2026-11-04T10:00:00+08:00','attempts':1,'code':'transport_or_json'}
  self.assertFalse(retry_allowed(old,datetime.fromisoformat('2026-11-04T10:59:00+08:00')))
  self.assertTrue(retry_allowed(old,datetime.fromisoformat('2026-11-04T11:01:00+08:00')))
  self.assertFalse(retry_allowed(dict(old,attempts=3),datetime.fromisoformat('2026-11-04T15:00:00+08:00')))
  self.assertFalse(retry_allowed(dict(old,code=40100),datetime.fromisoformat('2026-11-04T15:00:00+08:00')))
 def test_bounded_windows_include_recent_and_rotate_old_gaps(self):
  from qc_shadow.gap_recovery import choose_windows
  from datetime import date,timedelta
  days=[str(date(2025,1,1)+timedelta(days=i)) for i in range(365)]
  a=choose_windows(days,date(2026,11,4));b=choose_windows(days,date(2026,11,5))
  self.assertLessEqual(len(a),6);self.assertEqual(max(stop for _,stop in a),date(2025,12,31));self.assertNotEqual(a,b)
 def test_recovery_replaces_invalid_copy_not_canonical(self):
  from qc_shadow.plan_audit import apply_recovery
  p={'plan_id':'2','daily':[{'day':'2026-11-03','spend':'1','gmv':'0','orders':0,'complete':False}]}
  row={'day':'2026-11-03','spend':'1','gmv':'2','orders':1,'complete':True,'daily_aggregate_matches':True,'evidence':'e'}
  q=apply_recovery(p,[{'day':'2026-11-03','status':'recovered_shadow_only','row':row}]);self.assertEqual(q['daily'],[row]);self.assertFalse(p['daily'][0]['complete'])
