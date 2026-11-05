import unittest
from datetime import date,timedelta
from qc_shadow.roi_shadow import analyze_plan
class RoiTests(unittest.TestCase):
 def fixture(self):
  rows=[{'day':str(date(2026,10,28)+timedelta(days=i)),'spend':'10','gmv':'100','orders':5,'complete':True,'daily_aggregate_matches':True,'evidence':'day'} for i in range(7)]
  p={'plan_id':'p','scope':'chuanfang_goods','daily':rows,'refund':{'rate':'0.2','verified':True,'band':'201_300','model_version':'1'},'observations':[]}
  settings={'plan_id':'p','status':'ok','fetched_at':'2026-11-04T10:35:10+08:00','name':'test','target_roi':'6','budget':'5000','platform_status':'DELIVERY_OK','opt_status':'ENABLE','smart_bid_type':'SMART_BID_CUSTOM','alliance_commision_switch':'OFF','star_task_material_switch':'OFF','overall_roi_cost_items':[],'recommendation_basis_verified':True,'recommended_roi':'5','recommendation_status':'ok','evidence':'api'}
  return p,settings
 def test_complete_profitable_shadow_candidate_no_execution(self):
  p,s=self.fixture();r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00')
  self.assertEqual(r['windows']['7']['spend'],'70');self.assertFalse(r['roi_write_enabled']);self.assertFalse(r['plan_pause_enabled']);self.assertEqual(r['advice'],'hold_observe');self.assertIsNone(r['candidate_roi'])
 def test_loss_prevents_lowering(self):
  p,s=self.fixture();p['daily'][-1].update(spend='500',gmv='100')
  r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertEqual(r['advice'],'do_not_lower_loss');self.assertIsNone(r['candidate_roi'])
 def test_missing_row_not_zero_or_full_window(self):
  p,s=self.fixture();p['daily'].pop();r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertIsNone(r['windows']['7']['spend']);self.assertEqual(r['advice'],'observe_data_gap')
 def test_cost_flags_conflict_blocks_numeric_recommendation(self):
  p,s=self.fixture();s['overall_roi_cost_items']=[3,4];r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertFalse(r['cost_context_verified']);self.assertIsNone(r['candidate_roi']);self.assertIn('roi_cost_flags_conflict',r['gaps'])
 def test_failed_or_stale_setting_does_not_reuse_target(self):
  for state in ['failed','ok']:
   p,s=self.fixture();s['status']=state;s['fetched_at']='2026-11-03T10:35:10+08:00';r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertIsNone(r['current_target_roi']);self.assertIsNone(r['candidate_roi'])
 def test_budget_limited_is_not_roi_lowering(self):
  p,s=self.fixture();s['platform_status']='OFFLINE_BUDGET';r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertEqual(r['advice'],'budget_path_only');self.assertIsNone(r['candidate_roi'])
 def test_unknown_intraday_not_zero_and_revision_not_rate(self):
  p,s=self.fixture();r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertIsNone(r['intraday']['spend'])
  p['observations']=[{'business_date':'2026-11-04','fetched_at':'2026-11-04T10:00:00+08:00','spend':'100','gmv':'200','paid_orders':'2'},{'business_date':'2026-11-04','fetched_at':'2026-11-04T10:30:00+08:00','spend':'90','gmv':'200','paid_orders':'2'}]
  self.assertIsNone(analyze_plan(p,s,'2026-11-04T10:36:00+08:00')['intraday']['spend_rate_per_hour'])
 def test_missing_recommendation_no_invented_candidate(self):
  p,s=self.fixture();s['recommended_roi']=None;s['recommendation_status']='failed';r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertIsNone(r['candidate_roi'])

 def test_unreconciled_profitable_window_blocks_candidate(self):
  p,s=self.fixture();p['daily'][0]['daily_aggregate_matches']=False
  r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertIsNone(r['candidate_roi'])

 def test_no_numeric_target_from_old_recommendation(self):
  p,s=self.fixture();s.update(target_roi='6.11',recommended_roi='5.8049')
  r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertIsNone(r['candidate_roi'])

 def test_recommendation_basis_is_not_a_required_input(self):
  p,s=self.fixture();s.pop('recommendation_basis_verified')
  r=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertIsNone(r['candidate_roi']);self.assertNotIn('recommendation_basis_unverified',r['gaps']);self.assertFalse(any('高于平台建议' in h for h in r['hypotheses']))
