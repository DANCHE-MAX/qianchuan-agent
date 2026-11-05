import tempfile, unittest
from pathlib import Path
from qc_shadow.execution_store import ExecutionStore
from qc_shadow.budget_execution import BudgetExecutor, REQUIRED
class ExecutionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=ExecutionStore(Path(self.tmp.name)/'a.db');self.s.freeze('a','p','300','2026-11-04','verified:budget')
  self.calls=[];self.facts={'account_id':'a','plan_id':'p','current':'300','checks':{k:True for k in REQUIRED},'evidence':'verified:decision','at':'2026-11-04T10:00:00+08:00'}
  self.ex=BudgetExecutor(self.s,{'a':{'p'}},lambda: '2026-11-04T10:00:01+08:00')
 def tearDown(self):self.tmp.cleanup()
 def write(self,a,p,target):
  self.calls.append((a,p,target));return {'code':0,'request_id':'r','data':{'results':[{'ad_id':p,'status':'SUCCESS'}]}}
 def run_action(self,key='k',write=None,readback=None):
  return self.ex.execute(key,'a','p',lambda:self.facts,write or self.write,readback or (lambda a,p:'400'))
 def test_success_repeat_and_count(self):
  self.assertEqual(self.run_action()['status'],'success');self.assertEqual(self.run_action()['status'],'duplicate_or_uncertain');self.assertEqual(len(self.calls),1);self.assertEqual(self.s.success_count('a','p'),1)
 def test_each_missing_gate_blocks(self):
  for gate in REQUIRED:
   self.facts['checks'][gate]=False
   self.assertEqual(self.run_action(gate)['status'],'blocked');self.facts['checks'][gate]=True
  self.assertEqual(self.calls,[])
 def test_timeout_blocks_all_future_intents(self):
  def timeout(*args):raise TimeoutError()
  self.assertEqual(self.run_action(write=timeout)['status'],'uncertain')
  self.assertEqual(self.run_action('next')['status'],'duplicate_or_uncertain');self.assertEqual(self.calls,[])
 def test_code_zero_without_plan_success_uncertain(self):
  self.assertEqual(self.run_action(write=lambda *a:{'code':0,'data':{}})['status'],'uncertain')
 def test_readback_mismatch_uncertain(self):
  self.assertEqual(self.run_action(readback=lambda *a:'300')['status'],'uncertain');self.assertEqual(self.s.success_count('a','p'),0)
 def test_wrong_identity_and_stale(self):
  self.facts['plan_id']='other';self.assertEqual(self.run_action()['status'],'blocked')
  self.facts['plan_id']='p';self.facts['at']='2026-11-03T10:00:00+08:00';self.assertEqual(self.run_action('stale')['status'],'blocked')
 def test_cap_and_no_decrease(self):
  self.facts['current']='740';self.assertEqual(self.run_action(readback=lambda *a:'750')['target'],'750.00')
  self.facts['current']='800';self.assertEqual(self.run_action('second')['status'],'blocked')
 def test_second_increase_requires_bound_approval(self):
  self.run_action();self.facts['current']='400'
  self.assertEqual(self.run_action('second')['status'],'approval_required')
 def test_future_stamp_and_bool_gate_not_admitted(self):
  self.facts['at']='2026-11-04T10:00:02+08:00';self.assertEqual(self.run_action()['status'],'blocked')
  self.facts['at']='2026-11-04T10:00:00+08:00';self.facts['checks']['refund_verified']=1;self.assertEqual(self.run_action('bool')['status'],'blocked')
 def approval(self,key='second'):
  return {'identity_verified':True,'status':'approved','account_id':'a','plan_id':'p','rule_version':'business-v1.0','request_id':key,'actor_id':'actor','expected_actor_id':'actor','evidence':'approval:verified','before':'400','after':'500','issued_at':'2026-11-04T09:00:00+08:00','expires_at':'2026-11-05T09:00:00+08:00'}
 def test_exact_bound_approval_success(self):
  self.run_action();self.facts['current']='400';self.facts['approval']=self.approval()
  self.assertEqual(self.run_action('second',readback=lambda *a:'500')['status'],'success')
 def test_expired_or_wrong_amount_approval_rejected(self):
  self.run_action();self.facts['current']='400';a=self.approval();a['expires_at']='2026-11-04T09:59:59+08:00';self.facts['approval']=a
  self.assertEqual(self.run_action('second')['status'],'approval_required')
  a=self.approval('third');a['after']='501';self.facts['approval']=a
  self.assertEqual(self.run_action('third')['status'],'approval_required')
 def test_day31_does_not_get_first_exemption(self):
  self.ex.clock=lambda:'2026-12-04T10:00:01+08:00';self.facts['at']='2026-12-04T10:00:00+08:00'
  self.assertEqual(self.run_action()['status'],'approval_required');self.assertEqual(self.calls,[])
 def test_day30_gets_first_exemption(self):
  self.ex.clock=lambda:'2026-12-03T10:00:01+08:00';self.facts['at']='2026-12-03T10:00:00+08:00'
  self.assertEqual(self.run_action()['status'],'success')
 def test_known_pre_submit_block_is_not_uncertain(self):
  from qc_shadow.budget_execution import BeforeSubmitBlocked
  def stop(*_):raise BeforeSubmitBlocked('disabled')
  self.assertEqual(self.run_action(write=stop)['status'],'blocked')
  self.assertEqual(self.s.success_count('a','p'),0)
