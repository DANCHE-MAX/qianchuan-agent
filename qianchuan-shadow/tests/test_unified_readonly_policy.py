import unittest
from qc_shadow.profit_policy import estimate,shadow_policy
from qc_shadow.daily_rules import assess_daily
class UnifiedTests(unittest.TestCase):
 def test_readonly_profit_and_quality_agree_on_borderline(self):
  refund=dict(rate='.6',band='demo',model_version='v1',verified=True)
  p=shadow_policy(refund);self.assertEqual(estimate('1000','222.62',p)['profit'],'-0.02')
  from decimal import Decimal
  rows=[dict(day='2026-01-01',gmv='1000',spend='222.62',orders=2,complete=True,evidence='synthetic')]
  q=assess_daily(rows,'2026-01-02T10:00:00+08:00',refund,estimator=lambda g,s:Decimal(estimate(g,s,p)['profit']))
  self.assertFalse(q['last_day_profitable'])
  # Execution consumers keep their original estimator unless explicitly opted in.
  self.assertTrue(assess_daily(rows,'2026-01-02T10:00:00+08:00',refund)['last_day_profitable'])
