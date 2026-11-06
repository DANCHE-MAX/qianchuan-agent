import json,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,date
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import generation,profit_analysis,monitor
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  (self.root/'config').mkdir();(self.root/'config/daily_brief.json').write_text('{"profit_report":true}')
  (self.root/'config/rules.json').write_text('{}');self.out=self.root/'out';self.out.mkdir()
  self.env={'QC_ADVERTISER_IDS':'123','FEISHU_CHAT_ID':'chat'}
  self.context={'chat_id':'chat','sender_id':'user','create_time':str(int(datetime(2026,1,3,tzinfo=generation.CHINA).timestamp()*1000))}
  clock=patch.object(generation.model,'china_today',return_value=date(2026,1,3));clock.start();self.addCleanup(clock.stop)
  self.report={'account':'123','date':'2026-01-01','version':'qianchuan-plan-scenario-v1','rows':[],'calculable':0,'missing_profit':0,'totals_calculable_only':{}}
 def prepared(self):
  folder=self.out/'profit_daily/123/2026-01-01/generated';folder.mkdir(parents=True,exist_ok=True)
  (folder/'result.json').write_text(json.dumps(self.report));img=folder/'profit-2026-01-01-01.png';img.write_bytes(b'synthetic png')
  return {'directory':str(folder),'files':[{'kind':'image','path':str(img)}]}
 def test_enabled_explicit_generation_replies_image_not_old_cards(self):
  nf=Mock()
  with patch('agent.manual_profit.obtain',side_effect=lambda *args:self.prepared()),patch('agent.manual_profit.deliver',return_value={}) as send:
   generation.reply(nf,'msg','生成2026-01-01日报',self.root,self.out,self.env,self.context)
  self.assertEqual(send.call_count,1);self.assertEqual(send.call_args.args[1],'msg');self.assertEqual(send.call_args.args[2],'chat')
  nf.reply_card.assert_not_called();nf.send_card.assert_not_called()
 def test_retry_reuses_prepared_payload(self):
  nf=Mock()
  with patch('agent.manual_profit.obtain',side_effect=lambda *args:self.prepared()) as obtain,patch('agent.manual_profit.deliver',side_effect=[TimeoutError(),{}]):
   with self.assertRaises(TimeoutError):generation.reply(nf,'msg','生成2026-01-01日报',self.root,self.out,self.env,self.context)
   generation.reply(nf,'msg','生成2026-01-01日报',self.root,self.out,self.env,self.context)
   self.assertEqual(obtain.call_count,1)
  result=profit_analysis.load(self.out,'123','2026-01-01');self.assertEqual(result['date'],'2026-01-01')
 def test_failure_never_uses_old_model(self):
  nf=Mock()
  with patch('agent.manual_profit.obtain',side_effect=RuntimeError('private-secret')),patch('agent.manual_profit.deliver') as send:
   generation.reply(nf,'msg','生成2026-01-01日报',self.root,self.out,self.env,self.context)
  text=str(nf.reply_card.call_args);self.assertIn('待核验',text);self.assertNotIn('private-secret',text);send.assert_not_called()
 def test_today_rejected_before_fetch(self):
  nf=Mock()
  with patch('agent.manual_profit.obtain') as obtain:
   generation.reply(nf,'msg','生成2026-01-03日报',self.root,self.out,self.env,self.context)
  obtain.assert_not_called();self.assertIn('完整自然日',str(nf.reply_card.call_args))
 def test_multiple_image_pages_use_distinct_reply_uuids(self):
  from agent import profit_summary
  nf=Mock();nf.reply_image.side_effect=[{'data':{'message_id':'m0'}},{'data':{'message_id':'m1'}}]
  nf.get_message.side_effect=[[{'chat_id':'chat','msg_type':'image','body':{'content':json.dumps({'image_key':'img0'})}}],
                             [{'chat_id':'chat','msg_type':'image','body':{'content':json.dumps({'image_key':'img1'})}}]]
  with patch.object(profit_summary,'upload',side_effect=['img0','img1']):
   for i in range(2):
    path=self.out/('report-%02d.png'%i);path.write_bytes(b'synthetic')
    profit_summary.deliver(nf,'same-parent','chat',path)
  ids=[c.args[2] for c in nf.reply_image.call_args_list];self.assertNotEqual(ids[0],ids[1])
 def test_both_balance_categories_use_only_existing_operator(self):
  cfg={'notify':{'roles':{'投手':['ou_synthetic'],'老板':[]}},'monitoring':{'at_roles':{'balance_check':['投手'],'balance_alert':['投手']}}}
  for category in ['balance_check','balance_alert']:
   card=monitor.add_mentions({'elements':[]},cfg,category)
   self.assertIn('<at id="ou_synthetic">',json.dumps(card,ensure_ascii=False).replace('\\"','"'))

if __name__=='__main__':unittest.main()
