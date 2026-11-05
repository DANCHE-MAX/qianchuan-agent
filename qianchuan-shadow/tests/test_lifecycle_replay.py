import unittest
from datetime import date,timedelta
from qc_shadow.lifecycle_replay import replay
class ReplayTests(unittest.TestCase):
 def rows(self,n):
  return [{'day':str(date(2026,10,20)+timedelta(days=i)),'spend':'100','gmv':'0','orders':1} for i in range(n)]
 def test_day15_protection_after_launch_blocks_budget(self):
  rows=self.rows(15)
  for r in rows[4:7]:r['gmv']='200'
  result=replay(rows,'2026-10-20',date(2026,11,3),'0','2026-10-20')
  self.assertIn('current_or_pending_protection',result['budget_vetoes'])
 def test_expired_pause_records_not_execute(self):
  rows=self.rows(35);result=replay(rows,'2026-10-20',date(2026,11,23),'0','2026-11-04')
  self.assertTrue(any(e['action']=='would_pause' for e in result['events']))
  self.assertEqual(result['budget_vetoes'],[]);self.assertTrue(all(not e['production_action'] for e in result['events']))
 def test_current_day30_loss_blocks(self):
  rows=self.rows(30)
  for r in rows[4:7]:r['gmv']='200'
  result=replay(rows,'2026-10-20',date(2026,11,18),'0','2026-11-04')
  self.assertIn('T30',result['budget_vetoes'])
