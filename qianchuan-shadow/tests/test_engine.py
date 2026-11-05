import copy, unittest
from datetime import date,datetime,timedelta
from decimal import Decimal
from qc_shadow.engine import evaluate
from qc_shadow.contracts import Evaluation

def fixture(days=3, spend='10', gmv='0',orders=0,rule_day=3):
 end=date(2026,11,1); start=end-timedelta(days=days-1)
 return dict(account_id='999',plan_id='123',scope='chuanfang_goods',as_of='2026-11-02T10:00:00+08:00',
  simulation=True,rule_live_on=(date(2026,11,2)-timedelta(days=rule_day-1)).isoformat(),
  first_enabled_on=start.isoformat(),first_enabled_evidence='fixture:first',
  daily=[dict(day=(start+timedelta(days=i)).isoformat(),spend=spend,gmv=gmv,orders=orders,complete=True,evidence='fixture:daily') for i in range(days)],
  refund_rate='0',refund_band='fixture',model_version='fixture-v1',refund_verified=True,
  state={'stage':'initial' if days==3 else 'to7' if days==7 else 'passed'},
  budget={'baseline':'300','current':'300','mode':'day','baseline_evidence':'fixture:B','verified':True,'quantum':'0.01','precision_verified':True},
  balance={'available':'1000','verified':True},platform_active=True,platform_verified=True,
  today_spend='270',intraday_verified=True)
def run(d):return evaluate(Evaluation.model_validate(d))
class EngineTests(unittest.TestCase):
 def test_t03_threshold(self):
  for spend,rule in [('9.66','T03-B'),('10','T03-A')]:
   self.assertIn(rule,run(fixture(spend=spend))['rules'])
 def test_rule_day31_zero_sales(self):self.assertIn('T03-C',run(fixture(spend='0.33',rule_day=31))['rules'])
 def test_missing_not_zero(self):
  d=fixture();d['daily'].pop();self.assertEqual(run(d)['action'],'defer')
 def test_refund_missing_zero_sales_still_pause(self):
  d=fixture();d['refund_rate']=None;self.assertEqual(run(d)['action'],'pause')
 def test_refund_missing_sales_blocks_loss_pause(self):
  d=fixture(gmv='1',orders=1);d['refund_rate']=None;self.assertEqual(run(d)['action'],'defer')
 def test_day7_zero_sales(self):self.assertIn('T07-A',run(fixture(days=7))['rules'])
 def test_day7_three_profitable_extends(self):
  d=fixture(days=7,spend='100',gmv='0',orders=1)
  for day in d['daily'][-3:]:day['gmv']='200'
  self.assertIn('T07-B',run(d)['rules'])
 def test_day7_pause_beats_growth(self):
  d=fixture(days=7,spend='100',gmv='0',orders=1)
  for day,n in zip(d['daily'][-2:],[5,10]):day.update(gmv='200',orders=n)
  self.assertEqual(run(d)['action'],'pause');self.assertIn('T07-C',run(d)['rules'])
 def good(self):
  d=fixture(days=7,spend='10',gmv='100',orders=5);d['state']['stage']='passed';return d
 def test_growth_five_ten(self):
  d=self.good();d['daily'][-1]['orders']=10
  self.assertIn('Q-GROW',run(d)['rules']);self.assertEqual(run(d)['target_budget'],'400')
 def test_growth_one_two_not_qualified(self):
  d=fixture(days=2,spend='10',gmv='100',orders=1);d['daily'][-1]['orders']=2
  self.assertIn('quality_not_met',run(d)['blockers'])
 def test_zero_day_not_stable(self):
  d=self.good();d['daily'][2].update(gmv='0',spend='0');self.assertNotIn('Q-STABLE',run(d)['rules'])
 def test_balance500_invalidates(self):
  d=self.good();d['balance']['available']='500';d['state']['pending_request_id']='req1'
  r=run(d);self.assertIn('low_balance',r['blockers']);self.assertEqual(r['invalidated_requests'],['req1'])
 def test_cap_partial_final_increment(self):
  d=self.good();d['budget']['current']='740';d['today_spend']='740'
  self.assertEqual(run(d)['target_budget'],'750')
 def test_at_cap_no_increase(self):
  d=self.good();d['budget']['current']='750';d['today_spend']='750';self.assertNotEqual(run(d)['action'],'increase')
 def test_no_shadow_clock(self):
  d=self.good();d['simulation']=False;d['rule_live_on']=None
  r=run(d);self.assertIn('rule_clock_not_started',r['blockers']);self.assertFalse(r['execution_allowed'])
 def test_30day_protection_survives_month(self):
  d=fixture(days=20,spend='10',gmv='100',orders=5,rule_day=31);d['state'].update(protected_until='2026-11-11',protection_evidence='grant',protection_granted_at='2026-10-28T10:00:00+08:00',stage='to30')
  self.assertIn('observation_protection',run(d)['blockers'])
 def test_quality_loss_month_boundary(self):
  for day,action in [(30,'observe'),(31,'pause')]:
   d=self.good();d['rule_live_on']=(date(2026,11,2)-timedelta(days=day-1)).isoformat()
   for row in d['daily'][-3:]:row['gmv']='1'
   self.assertEqual(run(d)['action'],action)
 def test_adjustment_needs_full_day(self):
  d=self.good();d['budget']['last_changed_at']='2026-11-01T09:00:00+08:00'
  self.assertIn('budget_observation_day',run(d)['blockers'])
 def test_manual_change_invalidates_request(self):
  d=self.good();d['budget']['manual_changed']=True;d['state']['pending_request_id']='x'
  self.assertEqual(run(d)['invalidated_requests'],['x'])
 def test_second_increase_needs_approval(self):
  d=self.good();d['state']['assistant_increases']=1
  self.assertEqual(run(d)['action'],'request_approval')
 def test_no_free_pass_after_month(self):
  d=self.good();d['rule_live_on']='2026-09-01';self.assertEqual(run(d)['action'],'request_approval')
 def test_first_increase_ignores_manual_count(self):
  d=self.good();self.assertEqual(run(d)['action'],'increase')
 def test_disabled_never_resumes(self):
  d=self.good();d['platform_active']=False;self.assertNotEqual(run(d)['action'],'resume');self.assertNotEqual(run(d)['action'],'increase')
 def test_before10_defer(self):
  d=fixture();d['as_of']='2026-11-02T09:59:59+08:00';self.assertEqual(run(d)['action'],'defer')
 def test_determinism(self):
  d=self.good();self.assertEqual(run(d),run(copy.deepcopy(d)))
 def test_money_units_and_roi(self):
  r=run(self.good());self.assertEqual(r['metrics']['cumulative_spend'],'70');self.assertEqual(r['metrics']['payment_roi'],'10')
 def test_precision_unknown_blocks(self):
  d=self.good();d['budget']['precision_verified']=False;self.assertIn('budget_precision_unknown',run(d)['blockers'])
 def test_invalid_duplicate_days(self):
  d=fixture();d['daily'].append(d['daily'][0]);self.assertEqual(run(d)['action'],'defer')
 def test_missing_first_date(self):
  d=fixture();d['first_enabled_on']=None;self.assertIn('first_enabled_unknown',run(d)['blockers'])

class BoundaryTests(unittest.TestCase):
 good=EngineTests.good
 def test_t15_first_month_grants30(self):
  d=fixture(days=15,spend='100',gmv='1',orders=1,rule_day=20);d['state']['stage']='to15'
  self.assertIn('T15-A',run(d)['rules']);self.assertNotEqual(run(d)['action'],'increase')
 def test_t15_aftermonth_pause(self):
  d=fixture(days=15,spend='100',gmv='1',orders=1,rule_day=31);d['state']['stage']='to15'
  self.assertIn('T15-B',run(d)['rules']);self.assertEqual(run(d)['action'],'pause')
 def test_t30_loss_pause(self):
  d=fixture(days=30,spend='100',gmv='1',orders=1,rule_day=40);d['state'].update(stage='to30',protected_until='2026-11-01',protection_evidence='grant',protection_granted_at='2026-10-18T10:00:00+08:00')
  self.assertIn('T30',run(d)['rules'])
 def test_recovery_only_exempts_original_rule(self):
  d=fixture();d['state'].update(recovery_until='2026-11-03',recovery_evidence='approval',exempt_rules=['T03-A'])
  self.assertNotEqual(run(d)['action'],'pause');self.assertIn('recovery_observation',run(d)['blockers'])
  d['state']['exempt_rules']=['QUALITY-LOSS-3D'];self.assertEqual(run(d)['action'],'pause')
 def test_late_node_not_executed(self):
  d=fixture(days=5);d['state']['stage']='initial';self.assertEqual(run(d)['action'],'defer');self.assertIn('late_node_policy_unresolved',run(d)['blockers'])
 def approval_input(self):
  d=self.good();d['state'].update(assistant_increases=1,pending_request_id='r1')
  d['approval']=dict(request_id='r1',account_id='999',plan_id='123',before='300',after='400',rule_version='business-v1.0',issued_at='2026-11-02T09:00:00+08:00',expires_at='2026-11-03T09:00:00+08:00',status='approved',actor_id='trader',expected_actor_id='trader',identity_verified=True,evidence='signed:fixture')
  return d
 def test_valid_approval_binding(self):self.assertEqual(run(self.approval_input())['action'],'increase')
 def test_wrong_actor_stale_wrong_amount(self):
  for key,value in [('actor_id','outsider'),('before','301'),('expires_at','2026-11-02T10:00:00+08:00'),('status','invalidated')]:
   d=self.approval_input();d['approval'][key]=value;self.assertEqual(run(d)['action'],'request_approval')
 def test_naive_datetime_nan_rejected(self):
  from pydantic import ValidationError
  d=fixture();d['as_of']='2026-11-02T10:00:00'
  with self.assertRaises(ValidationError):run(d)
  d=fixture();d['daily'][0]['spend']='NaN'
  with self.assertRaises(ValidationError):run(d)
