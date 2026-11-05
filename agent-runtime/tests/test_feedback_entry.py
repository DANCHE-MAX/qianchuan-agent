import json,sys,tempfile,unittest,copy
from pathlib import Path
from datetime import timedelta
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import feedback,listener
from agent.feedback_store import Ledger
from test_feedback_loop import NOW,CFG,ENV,spec,message
class EntryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'config').mkdir()
  (self.root/'config/rules.json').write_text(json.dumps(CFG));self.db=Ledger(self.root/'data/feedback_loop.db');self.svc=feedback.Service(self.db,CFG,ENV)
  self.p=self.svc.create(spec(),now=NOW);self.pid=self.p['id'];self.n=Mock();self.n.BASE='https://open.feishu.cn/open-apis';self.n._tenant_token.return_value='fake'
  self.context={'chat_id':'oc_test','sender_id':'ou_test','advertiser_id':'999'}
 def bind(self):self.db.reserve_delivery(self.pid);self.db.finish_delivery(self.pid,'om_proposal')
 def api(self,text,**kw):self.n._request.return_value={'code':0,'data':{'items':[message(self.pid,text,**kw)]}}
 def test_listener_routes_and_authenticates_via_read_api(self):
  self.bind();cmd=f'反馈 {self.pid} 采纳';self.api(cmd)
  with patch.object(feedback,'clock',return_value=NOW+timedelta(minutes=2)):
   listener.reply_command(self.n,'om_feedback',cmd,outdir=self.root/'out',env=ENV,context=self.context)
  self.assertEqual(self.db.events(self.pid)[0]['action'],'adopted')
  self.assertEqual(self.n._request.call_args.args[0],'GET');self.assertIn('user_id_type=open_id',self.n._request.call_args.args[1])
  self.assertIn('仅记录',self.n.reply_text.call_args.args[1])
 def test_event_sender_cannot_override_api_sender(self):
  self.bind();cmd=f'反馈 {self.pid} 采纳';self.api(cmd,sender={'id':'ou_intruder','id_type':'open_id','sender_type':'user'})
  with patch.object(feedback,'clock',return_value=NOW+timedelta(minutes=2)):
   feedback.reply(self.n,'om_feedback',cmd,self.root,ENV,self.context)
  self.assertEqual(self.db.events(self.pid),[])
 def test_wrong_scope_blocks_before_api(self):
  feedback.reply(self.n,'om_feedback','反馈列表',self.root,ENV,dict(self.context,chat_id='oc_elsewhere'))
  self.n._request.assert_not_called();self.assertFalse(self.n.reply_text.called)
 def test_list_and_detail_do_not_read_budget(self):
  self.api('反馈列表')
  with patch.object(feedback,'clock',return_value=NOW+timedelta(minutes=2)),patch('agent.budgets.fetch') as fetch:
   feedback.reply(self.n,'om_feedback','反馈列表',self.root,ENV,self.context)
  fetch.assert_not_called();self.assertIn(self.pid,self.n.reply_text.call_args.args[1])
 def test_sending_uncertain_is_not_replayed(self):
  self.n._request.side_effect=TimeoutError('private')
  result=feedback.publish(self.svc,self.pid,self.n)
  self.assertEqual(result['status'],'uncertain');self.assertEqual(feedback.publish(self.svc,self.pid,self.n)['status'],'uncertain')
  self.assertEqual(self.n._request.call_count,1)
 def test_publish_uses_configured_mentions_and_message_receipt(self):
  self.n._request.return_value={'code':0,'data':{'message_id':'om_proposal'}}
  self.assertEqual(feedback.publish(self.svc,self.pid,self.n)['status'],'sent')
  self.assertEqual(self.db.receipt(self.pid),'om_proposal');self.assertIn('<at user_id="ou_test">',json.loads(self.n._request.call_args.args[2]['content'])['text'])
  feedback.publish(self.svc,self.pid,self.n);self.assertEqual(self.n._request.call_count,1)
 def test_read_api_failure_does_not_store_feedback_or_expose_error(self):
  self.bind();self.n._request.side_effect=RuntimeError('private-key')
  feedback.reply(self.n,'om_feedback',f'反馈 {self.pid} 采纳',self.root,ENV,self.context)
  self.assertEqual(self.db.events(self.pid),[]);self.assertNotIn('private-key',self.n.reply_text.call_args.args[1])
if __name__=='__main__':unittest.main()
