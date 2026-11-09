import unittest,copy
from agent import plan_profit

class UnifiedPolicyTests(unittest.TestCase):
 def policy(self):
  return dict(refund_rate='.6',purchase_multiplier='3.2',fee_rate='.131',ad_rate='.15',shipping_mode='excluded',model_version='unified-readonly-v1',plan_refunds={'demo':{'rate':'.7','band':'high','source':'confirmed'}})
 def test_plan_rate_is_used_and_fallback_is_visible(self):
  p=self.policy();report=plan_profit.report([], [dict(plan_id='demo',name='A',date='2026-01-01',gmv='1000',spend='100'),dict(plan_id='other',name='B',date='2026-01-01',gmv='1000',spend='100')],[],p,date='2026-01-01')
  rows={r['plan_id']:r for r in report['rows']}
  self.assertEqual(rows['demo']['profit'],'66.95')
  self.assertEqual(rows['other']['profit'],'122.60')
  self.assertTrue(rows['other']['policy']['refund_fallback'])
 def test_legacy_calculation_unchanged(self):
  p=self.policy();p.pop('model_version');p.pop('plan_refunds')
  self.assertEqual(plan_profit.calculate('1000','100',p)['profit'],'122.60')
 def test_shared_estimator_and_missing(self):
  from agent.profit_policy import estimate,resolve_policy,scenario
  p=resolve_policy(self.policy(),'demo');self.assertEqual(estimate('1000','100',p)['profit'],'66.95')
  self.assertIsNone(estimate(None,'0',p)['profit'])
  self.assertEqual(scenario('166.95',p)['required_gmv'],'1000.00')
  with self.assertRaises(ValueError):scenario('-1',p)
 def test_bad_rate_rejected(self):
  from agent.profit_policy import resolve_policy
  p=self.policy();p['plan_refunds']['demo']['rate']='NaN'
  with self.assertRaises(ValueError):resolve_policy(p,'demo')
 def test_preflight_rejects_row_policy_tampering(self):
  from agent.report_preflight import inspect_report
  p=self.policy();r=plan_profit.report([], [dict(plan_id='demo',name='A',date='2026-01-01',gmv='1000',spend='100')],[],p,date='2026-01-01');r.update(account='test',policy=p)
  r['rows'][0]['policy']['refund_rate']='.2';errors=[];inspect_report(r,'test',r['date'],errors,[],'test')
  self.assertIn('test:row_policy_binding',errors)
