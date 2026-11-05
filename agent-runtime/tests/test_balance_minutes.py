import unittest
from datetime import datetime,timedelta
from decimal import Decimal
from agent.balance_cycle import rate
class Tests(unittest.TestCase):
 def samples(self):
  t=datetime.fromisoformat('2026-11-02T16:00:00+08:00')
  return [{'advertiser_id':'999','fetched_at':(t+timedelta(minutes=5*i)).isoformat(),'amounts_yuan':{'account_total':str(1000-10*i),'account_valid':str(1000-10*i),'account_frozen':'0'}} for i in range(5)]
 def test_twenty_minutes_actual_rate(self):
  s=self.samples();r=rate(s,s[-1],window_minutes=20,max_gap_minutes=7,tolerance_minutes=2)
  self.assertAlmostEqual(float(r['rate']),120);self.assertEqual(r['decline'],'40')
 def test_missing_middle_is_rejected(self):
  s=self.samples();s.pop(2)
  self.assertIsNone(rate(s,s[-1],window_minutes=20,max_gap_minutes=7,tolerance_minutes=2)['rate'])
 def test_recharge_is_rejected(self):
  s=self.samples();s[2]['amounts_yuan']['account_total']='2000'
  self.assertIsNone(rate(s,s[-1],window_minutes=20,max_gap_minutes=7,tolerance_minutes=2)['rate'])
