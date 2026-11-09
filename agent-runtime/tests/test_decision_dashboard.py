import unittest
from agent import decision_dashboard as dash
class DashboardTests(unittest.TestCase):
 def report(self,day,profit='10',gmv='100',source='api'):
  return dict(account='demo',date=day,policy={'refund_rate':'.6','purchase_multiplier':'3.2','fee_rate':'.131','ad_rate':'.15','shipping_mode':'excluded'},rows=[dict(plan_id='p',name='<商品>',gmv=gmv,spend='10',profit=profit,gmv_source=source)])
 def test_missing_days_and_values_do_not_become_zero(self):
  r=dash.build_trends([self.report('2026-01-07'),self.report('2026-01-06',profit=None)],{},'2026-01-07')
  self.assertEqual(len(r),7);self.assertIsNone(r[0]['profit']);self.assertIsNone(r[-2]['profit']);self.assertEqual(r[-1]['profit'],'10.00')
 def test_scope_collision_rejected(self):
  with self.assertRaises(ValueError):dash.build_trends([self.report('2026-01-07'),self.report('2026-01-07')],{},'2026-01-07')
 def test_quality_is_not_accuracy_and_imputation_visible(self):
  self.assertEqual(dash.quality({'gmv':'0','spend':'0','profit':'0','gmv_source':'business_rule_zero_spend'}),'含业务补值')
  self.assertEqual(dash.quality({'gmv':None,'spend':'0','profit':None}),'待核验')
 def test_partial_product_not_complete_and_managed_separate(self):
  r=self.report('2026-01-07');r['rows'] += [dict(plan_id='q',name='other',gmv=None,spend='2',profit=None),dict(plan_id='z',name='全店托管',gmv='5',spend='1',profit='1')]
  m={'day':r['date'],'account':'demo','rows':[{'plan':'p','product':'a','name':'商品'},{'plan':'q','product':'a','name':'商品'}]}
  groups=dash.groups(r,m);g=next(x for x in groups if x['id']=='product:a');self.assertIsNone(g['profit']);self.assertEqual(g['spend'],'12.00');self.assertEqual(len(groups),2)

class ShadowScopeTests(unittest.TestCase):
 def test_foreign_account_shadow_excluded(self):
  import tempfile,json
  from pathlib import Path
  from agent.decision_dashboard import build
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);shadow=root/'shadow';(shadow/'evidence/roi').mkdir(parents=True)
   (shadow/'evidence/roi/latest.json').write_text(json.dumps({'plans':[{'account_id':'other','plan_id':'demo','windows':{'1':{'start':'2026-01-07','end':'2026-01-07','estimated_profit':'100'}}}]}))
   value=build(root,'demo','2026-01-07',shadow)
   self.assertEqual(value['shadow'],[])
   self.assertTrue(any('账户范围' in w for w in value['warnings']))
