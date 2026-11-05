import unittest
from qc_shadow.plan_audit import audit_plan,revision_packets,diagnose
class AuditTests(unittest.TestCase):
 def test_missing_dates_include_unreconciled_not_zero(self):
  p={'plan_id':'2','first_enabled_on':'2026-11-01','daily':[{'day':'2026-11-01','complete':True,'daily_aggregate_matches':True,'evidence':'e','spend':'0','gmv':'0','orders':0},{'day':'2026-11-02','complete':True,'daily_aggregate_matches':False,'evidence':'e','spend':'0','gmv':'0','orders':0}]}
  a=audit_plan(p,'2026-11-04T12:00:00+08:00',[]);self.assertEqual(a['missing_daily_dates'],['2026-11-02','2026-11-03']);self.assertEqual(a['date_gaps'][0]['reason'],'unreconciled')
 def test_unknown_first_date_not_empty_history(self):
  a=audit_plan({'plan_id':'2','daily':[]},'2026-11-04T12:00:00+08:00',[]);self.assertIsNone(a['missing_daily_dates']);self.assertEqual(len(a['recent_missing_dates']),7)
 def test_revision_review_does_not_approve_stable_data(self):
  r={'plan_id':'2','day':'2026-11-03','before':{'spend':'10','gmv':'100','orders':2},'after':{'spend':'10','gmv':'90','orders':1}}
  x=revision_packets([r],[dict(r['after'],plan_id='2',day='2026-11-03',complete=True,daily_aggregate_matches=True,evidence='proof')])[0]
  self.assertTrue(x['checks']['current_matches_after']);self.assertEqual(x['review_status'],'待人审');self.assertFalse(x['execution_clearance'])
 def data(self):return {'windows':{'1':{'complete':True,'spend':'0.10','gmv':'0','orders':0,'estimated_profit':'-0.10'}},'intraday':{'fresh':True,'spend':'0.05','budget_ratio':'0.00001','spend_rate_per_hour':'0.01'},'gaps':[],'settings_evidence':'e'}
 def test_tiny_no_sales_not_efficiency_failure(self):
  d=diagnose(self.data(),{'status':'ok','opt_status':'ENABLE','platform_status':'DELIVERY_OK'});self.assertEqual(d['category'],'no_sales_sample');self.assertEqual(d['causal_confidence'],'未确认')
 def test_budget_near_limit_is_not_platform_confirmed(self):
  r=self.data();r['intraday']['budget_ratio']='0.95';d=diagnose(r,{'status':'ok','opt_status':'ENABLE','platform_status':'DELIVERY_OK'});self.assertEqual(d['category'],'budget_near_limit');self.assertEqual(d['causal_confidence'],'候选')
 def test_stale_status_does_not_prove_platform_limit(self):
  r=self.data();r['gaps']=['settings_unavailable_or_stale'];d=diagnose(r,{'status':'ok','opt_status':'DISABLE','platform_status':'OFFLINE_BUDGET'});self.assertEqual(d['category'],'settings_unknown')
 def test_sales_with_model_loss(self):
  r=self.data();r['windows']['1'].update(orders=8,spend='582.66',gmv='3192',estimated_profit='-44.81');d=diagnose(r,{'status':'ok','opt_status':'ENABLE','platform_status':'DELIVERY_OK'});self.assertEqual(d['category'],'sales_model_loss')
