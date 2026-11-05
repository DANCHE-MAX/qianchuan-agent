import unittest,copy
from qc_shadow.preflight import build_preflight
class PreflightTests(unittest.TestCase):
 def fixture(self):
  from datetime import date,timedelta
  rows=[{'day':str(date(2026,10,28)+timedelta(days=i)),'spend':'10','gmv':'100','orders':5,'complete':True,'evidence':'daily','daily_aggregate_matches':True} for i in range(7)]
  snapshot={'account_id':'a','as_of':'2026-11-04T10:00:00+08:00','plans':[{'plan_id':'p','scope':'chuanfang_goods','first_enabled_on':'2026-10-28','first_enabled_evidence':'log','daily':rows,'refund':{'verified':True,'rate':'0.1','band':'test','model_version':'1','verification_evidence':'user','expected_product_ids':['product']},'budget':{'amount':'300'}}]}
  live={'account_id':'a','plan_id':'p','started_at':'2026-11-04T10:00:00+08:00','at':'2026-11-04T10:00:10+08:00','detail':{'ad_id':'p','product_infos':[{'product_id':'product'}],'status':'DELIVERY_OK','opt_status':'ENABLE','marketing_goal':'VIDEO_PROM_GOODS','adlab_scene':1,'delivery_setting':{'budget':300,'budget_mode':'BUDGET_MODE_DAY'}},'today_spend':'270','available':'1000','logs_complete':True,'recent_logs':[],'log_start':'2026-11-02','evidence':'live:sha'}
  activation={'account_id':'a','authorized':True,'no_prior_protection_or_recovery':True,'live_on':'2026-11-04','historical_policy':'replay_only_current_decisions','plans':['p']}
  return snapshot,live,activation
 def test_verified_facts_admit(self):
  r=build_preflight(*self.fixture(),now='2026-11-04T10:00:11+08:00');self.assertTrue(all(r['checks'].values()),r['checks'])
 def test_gaps_unknown_refund_revisions_block(self):
  s,l,a=self.fixture();s['plans'][0]['daily'].pop();s['plans'][0]['refund']['verified']=False;s['pending_execution_revisions']=[{}]
  r=build_preflight(s,l,a,now='2026-11-04T10:00:11+08:00');self.assertFalse(r['checks']['lifecycle_verified']);self.assertFalse(r['checks']['refund_verified']);self.assertFalse(r['checks']['revisions_reviewed'])
 def test_low_balance_inactive_missing_spend(self):
  s,l,a=self.fixture();l['available']='500';l['detail']['opt_status']='DISABLE';l['today_spend']=None
  r=build_preflight(s,l,a,now='2026-11-04T10:00:11+08:00');self.assertFalse(r['checks']['balance_above_500']);self.assertFalse(r['checks']['platform_active']);self.assertFalse(r['checks']['budget_constrained'])
 def test_logs_changes_or_incomplete_block(self):
  for complete,logs in [(False,[]),(True,[{'object_id':'p','content_title':'修改'}])]:
   s,l,a=self.fixture();l.update(logs_complete=complete,recent_logs=logs)
   r=build_preflight(s,l,a,now='2026-11-04T10:00:11+08:00');self.assertFalse(r['checks']['observation_complete'])
 def test_no_early_daily_or_future_freshness(self):
  s,l,a=self.fixture()
  with self.assertRaises(ValueError):build_preflight(s,l,a,now='2026-11-04T09:59:00+08:00')
 def test_wrong_scope_budget_change_not_bypassed(self):
  s,l,a=self.fixture();l['detail']['delivery_setting']['budget']=400;l['detail']['marketing_goal']='LIVE_PROM_GOODS'
  r=build_preflight(s,l,a,now='2026-11-04T10:00:11+08:00');self.assertFalse(r['checks']['scope_verified']);self.assertFalse(r['checks']['budget_unchanged'])
 def test_current_day7_loss_blocks_even_growth_and_pause_off(self):
  s,l,a=self.fixture()
  for row in s['plans'][0]['daily'][:5]:row.update(spend='100',gmv='0',orders=0)
  s['plans'][0]['daily'][-1]['orders']=10
  r=build_preflight(s,l,a,now='2026-11-04T10:00:11+08:00')
  self.assertEqual(r['quality']['Q-GROW'],'met');self.assertFalse(r['checks']['lifecycle_verified'])
 def test_nested_and_malformed_logs_block(self):
  for logs in [[{'object_id':'other','sub_logs':[{'object_id':'p','content_title':'修改'}]}],[{}]]:
   s,l,a=self.fixture();l['recent_logs']=logs
   self.assertFalse(build_preflight(s,l,a,now='2026-11-04T10:00:11+08:00')['checks']['observation_complete'])
 def test_negative_available_balance_is_known_low(self):
  s,l,a=self.fixture();l['available']='-1'
  r=build_preflight(s,l,a,now='2026-11-04T10:00:11+08:00');self.assertFalse(r['checks']['balance_above_500'])

 def test_changed_product_binding_invalidates_refund_mapping(self):
  s,l,a=self.fixture();l['detail']['product_infos']=[{'product_id':'different'}]
  self.assertFalse(build_preflight(s,l,a,now='2026-11-04T10:00:11+08:00')['checks']['refund_verified'])
