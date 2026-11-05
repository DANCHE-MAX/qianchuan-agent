import json,sys,tempfile,unittest,copy,sqlite3
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import feedback
from agent.feedback_store import Ledger
NOW=datetime(2026,11,5,8,tzinfo=timezone.utc)
ENV={'QC_ADVERTISER_IDS':'999','FEISHU_CHAT_ID':'oc_test','FEISHU_ALLOWED_CHAT_IDS':'oc_test','QC_ACCESS_TOKEN':'fake-token'}
CFG={'notify':{'roles':{'投手':['ou_test']}}}
def spec(**kw):
 return dict(style='DEMO',plan_id='123',snapshot_at=NOW.isoformat(),model_version='test',budget_mode='BUDGET_MODE_DAY',target_budget='100',missing=[],**kw)
def message(pid,command,**kw):
 d={'message_id':'om_feedback','chat_id':'oc_test','parent_id':'om_proposal','msg_type':'text','deleted':False,'updated':False,'create_time':str(int((NOW+timedelta(minutes=1)).timestamp()*1000)),
 'sender':{'id':'ou_test','id_type':'open_id','sender_type':'user'},'body':{'content':json.dumps({'text':command})}}
 d.update(kw);return d
class FeedbackTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.ledger=Ledger(self.root/'feedback.db');self.svc=feedback.Service(self.ledger,CFG,ENV)
  self.p=self.svc.create(spec(),now=NOW);self.pid=self.p['id'];self.ledger.reserve_delivery(self.pid);self.ledger.finish_delivery(self.pid,'om_proposal')
 def submit(self,text,mid='om_feedback',**kw):
  m=message(self.pid,text,message_id=mid,**kw);return self.svc.record(m,now=NOW+timedelta(minutes=2))
 def execute(self):return self.submit(f'反馈 {self.pid} 已执行 100 {(NOW+timedelta(seconds=30)).isoformat()} 手动调整')
 def test_create_duplicate_and_preserve_missing(self):
  self.assertEqual(self.svc.create(spec(),now=NOW)['id'],self.pid)
  bad=spec();bad['missing']=['退款未完成'];bad['target_budget']=None
  p=self.svc.create(bad,now=NOW);self.assertIsNone(p['target_budget']);self.assertIn('退款未完成',p['missing'])
 def test_duplicate_message_and_revision_history(self):
  cmd=f'反馈 {self.pid} 采纳';self.submit(cmd);self.submit(cmd)
  self.assertEqual(len(self.ledger.events(self.pid)),1)
  self.submit(f'反馈 {self.pid} 暂缓 等素材',mid='om_second',create_time=str(int((NOW+timedelta(seconds=90)).timestamp()*1000)))
  self.assertEqual(len(self.ledger.events(self.pid)),2);self.assertEqual(self.ledger.events(self.pid)[-1]['action'],'deferred')
  self.assertEqual(Ledger(self.root/'feedback.db').events(self.pid),self.ledger.events(self.pid))
 def test_wrong_identity_chat_parent_and_bot_rejected(self):
  for kw in [{'chat_id':'oc_other'},{'parent_id':'om_other'},{'sender':{'id':'ou_other','id_type':'open_id','sender_type':'user'}},{'sender':{'id':'ou_test','id_type':'open_id','sender_type':'app'}},{'updated':True},{'deleted':True}]:
   with self.subTest(kw=kw),self.assertRaises(ValueError):self.submit(f'反馈 {self.pid} 采纳',**kw)
  self.assertEqual(self.ledger.events(self.pid),[])
 def test_missing_expired_and_changed_configuration_fail_closed(self):
  data=spec();data['missing']=['物流费用缺失'];p=self.svc.create(data,now=NOW);self.ledger.reserve_delivery(p['id']);self.ledger.finish_delivery(p['id'],'om_gap')
  with self.assertRaises(ValueError):self.submit(f"反馈 {p['id']} 采纳",parent_id='om_gap')
  with self.assertRaises(ValueError):self.svc.record(message(self.pid,f'反馈 {self.pid} 采纳'),now=NOW+timedelta(days=2))
  svc=feedback.Service(self.ledger,CFG,dict(ENV,QC_ADVERTISER_IDS='888'))
  with self.assertRaises(ValueError):svc.record(message(self.pid,f'反馈 {self.pid} 采纳'),now=NOW+timedelta(minutes=2))
 def test_invalid_money_time_and_out_of_order_rejected(self):
  for text in [f'修改后采纳 NaN 测试',f'已执行 -1 {NOW.isoformat()} 测试',f'已执行 100 2026-11-06T08:00:00+00:00 测试',f'已执行 100 2026-11-05T08:00:00 测试']:
   with self.subTest(text=text),self.assertRaises(ValueError):self.submit(f'反馈 {self.pid} '+text)
  self.submit(f'反馈 {self.pid} 采纳')
  with self.assertRaises(ValueError):self.submit(f'反馈 {self.pid} 拒绝 old',mid='om_old',create_time=str(int((NOW+timedelta(seconds=30)).timestamp()*1000)))
 def test_readback_match_is_not_execution_proof(self):
  self.execute();fetch=Mock(return_value={'advertiser_id':'999','plans':[{'plan_id':'123','status':'ok','mode':'BUDGET_MODE_DAY','amount':'100','fetched_at':(NOW+timedelta(minutes=2)).isoformat()}]})
  r=self.svc.verify(self.pid,now=NOW+timedelta(minutes=2),fetch=fetch)
  self.assertEqual(r['status'],'matched');self.assertFalse(r['execution_proven']);self.assertFalse(r['budget_write_allowed'])
  self.assertIn('不证明',feedback.render(self.svc.detail(self.pid)))
 def test_unavailable_mismatch_mode_and_stale_never_match(self):
  self.execute()
  base={'plan_id':'123','status':'ok','mode':'BUDGET_MODE_DAY','amount':'100','fetched_at':(NOW+timedelta(minutes=2)).isoformat()}
  for change,want in [({'amount':'200'},'mismatch'),({'mode':'BUDGET_MODE_TOTAL'},'unverified'),({'status':'unavailable','amount':None},'unverified'),({'amount':'NaN'},'unverified'),({'fetched_at':(NOW-timedelta(hours=1)).isoformat()},'unverified')]:
   fetch=Mock(return_value={'advertiser_id':'999','plans':[dict(base,**change)]})
   self.assertEqual(self.svc.verify(self.pid,now=NOW+timedelta(minutes=2),fetch=fetch)['status'],want)
 def test_no_self_report_does_not_query_api(self):
  fetch=Mock();r=self.svc.verify(self.pid,now=NOW,fetch=fetch);fetch.assert_not_called();self.assertEqual(r['status'],'unverified')
 def test_verify_timeout_is_missing_not_zero(self):
  self.execute();r=self.svc.verify(self.pid,now=NOW+timedelta(minutes=2),fetch=Mock(side_effect=TimeoutError('secret-value')))
  self.assertEqual(r['status'],'unverified');self.assertIsNone(r['observed_amount']);self.assertNotIn('secret-value',json.dumps(r))
 def test_unknown_group_configuration_and_unbound_proposal_rejected(self):
  with self.assertRaises(ValueError):feedback.Service(self.ledger,CFG,dict(ENV,FEISHU_ALLOWED_CHAT_IDS='oc_other'))
  p=self.svc.create(dict(spec(),style='OTHER'),now=NOW)
  with self.assertRaises(ValueError):self.submit(f"反馈 {p['id']} 采纳")
 def test_recreate_later_does_not_extend_expiry_or_duplicate(self):
  p=self.svc.create(spec(),now=NOW+timedelta(minutes=10))
  self.assertEqual(p['id'],self.pid);self.assertEqual(p['expires_at'],self.p['expires_at'])
 def test_feedback_change_during_readback_cannot_validate_new_event(self):
  self.execute()
  def fetch(*args):
   self.submit(f'反馈 {self.pid} 暂缓 等确认',mid='om_new',create_time=str(int((NOW+timedelta(seconds=100)).timestamp()*1000)))
   return {'advertiser_id':'999','plans':[{'plan_id':'123','status':'ok','mode':'BUDGET_MODE_DAY','amount':'100','fetched_at':(NOW+timedelta(minutes=2)).isoformat()}]}
  with self.assertRaises(ValueError):self.svc.verify(self.pid,now=NOW+timedelta(minutes=2),fetch=fetch)
  self.assertIsNone(self.svc.detail(self.pid)['check'])
 def test_actual_budget_adapter_uses_only_get_and_preserves_business_database(self):
  import io
  from agent import budgets
  self.execute();calls=[]
  def urlopen(req,**kwargs):
   calls.append(req)
   return io.BytesIO(json.dumps({'code':0,'data':{'ad_id':123,'delivery_setting':{'budget_mode':'BUDGET_MODE_DAY','budget':100}}}).encode())
  with patch('urllib.request.urlopen',side_effect=urlopen),patch.object(budgets,'now',return_value=(NOW+timedelta(minutes=2)).isoformat()):
   result=self.svc.verify(self.pid,now=NOW+timedelta(minutes=2))
  self.assertEqual(result['status'],'matched');self.assertEqual(len(calls),1)
  self.assertEqual(calls[0].get_method(),'GET');self.assertIn('/ad/detail/',calls[0].full_url)
  self.assertFalse((self.root/'app.db').exists())
 def test_old_message_cannot_be_replaced_with_new_content(self):
  self.submit(f'反馈 {self.pid} 采纳')
  with self.assertRaises(ValueError):self.submit(f'反馈 {self.pid} 拒绝 改了')
  self.assertEqual(self.ledger.events(self.pid)[0]['action'],'adopted')
 def test_old_data_cannot_be_reissued_as_fresh_budget_advice(self):
  data=spec();data['snapshot_at']=(NOW-timedelta(days=2)).isoformat()
  p=self.svc.create(data,now=NOW);self.ledger.reserve_delivery(p['id']);self.ledger.finish_delivery(p['id'],'om_old_data')
  with self.assertRaises(ValueError):self.submit(f"反馈 {p['id']} 采纳",parent_id='om_old_data')
 def test_events_are_immutable(self):
  self.execute()
  with self.ledger.db() as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM events')
if __name__=='__main__':unittest.main()
