import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import balance_cycle
from tests.test_balance_cycle import window

class CompactBalanceTests(unittest.TestCase):
 def text(self,values,rate=None):
  c=balance_cycle.notice(values[-1],rate or balance_cycle.rate(values,values[-1]))
  return c['elements'][0]['text']['content']
 def test_normal_notice_is_two_sentences(self):
  s=self.text(window());self.assertLess(len(s),160);self.assertLessEqual(s.count('。'),2);self.assertIn('暂不需要充值',s)
 def test_low_runway_warns_even_above_low_balance_threshold(self):
  s=self.text(window(valid='600',step='200'));self.assertIn('1.50小时',s);self.assertIn('及时充值',s)
 def test_unknown_does_not_claim_sufficient_balance(self):
  s=self.text(window(),{'rate':None,'window_hours':2,'reason':'窗口存在充值'})
  self.assertIn('暂无法估算',s);self.assertNotIn('暂不需要充值',s);self.assertLess(len(s),160)

class SummaryTests(unittest.TestCase):
 def report(self):
  return {'date':'2026-01-01','account':'123','calculable':2,'missing_profit':1,
   'totals_calculable_only':{'profit':'5.00','spend':'9.00'},'rows':[
    {'name':'示例盈利商品','plan_id':'1','spend':'8.00','profit':'7.00'},
    {'name':'示例亏损商品','plan_id':'2','spend':'1.00','profit':'-2.00'},
    {'name':'未知商品','plan_id':'3','spend':None,'profit':None}]}
 def test_exact_totals_focus_and_missing_are_preserved(self):
  from agent import profit_summary
  v=profit_summary.view(self.report());self.assertEqual(v['profit'],'5.00');self.assertEqual(v['spend'],'9.00')
  self.assertEqual(v['missing'],1);self.assertEqual(len(v['focus']),2);self.assertIn('整体',v['subhead'])
 def test_all_unknown_has_no_fake_zero_profit(self):
  from agent import profit_summary
  r=self.report();r.update(calculable=0,missing_profit=3,totals_calculable_only={'profit':None,'spend':None})
  r['rows']=[r['rows'][-1]];v=profit_summary.view(r)
  self.assertIsNone(v['profit']);self.assertIn('待核验',v['headline']);self.assertEqual(v['focus'],[])
 def test_detailed_questions_keep_text(self):
  from agent import profit_summary
  self.assertTrue(profit_summary.wants_image('昨日计划分析'))
  self.assertFalse(profit_summary.wants_image('详细分析昨日全部计划'))
 def test_image_reply_is_to_original_message(self):
  from agent.notify import FeishuAppNotifier
  n=FeishuAppNotifier('fake','fake','other-chat');n._tenant_token=lambda:'fake';n._request=Mock(return_value={})
  n.reply_image('original','img_fake','uuid')
  args=n._request.call_args.args;self.assertTrue(args[1].endswith('/original/reply'))
  self.assertEqual(args[2]['msg_type'],'image');self.assertEqual(json.loads(args[2]['content']),{'image_key':'img_fake'})
 def test_summary_does_not_depend_on_llm(self):
  from agent.profit_analysis import answer
  kb=Mock();kb.ask.side_effect=AssertionError('Summary must not call LLM')
  with patch('agent.profit_analysis.load',return_value=self.report()):
   text=answer('分析2026-01-01日报',Path('/synthetic/out'),{'QC_ADVERTISER_IDS':'123'},kb,summary_only=True)
  self.assertIn('2026-01-01',text);self.assertIn('5.00',text);kb.ask.assert_not_called()
  self.assertIn('报告',json.loads(kb.last_context))
 def test_readback_retry_never_sends_twice(self):
  from agent import profit_summary
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'sample.png';path.write_bytes(b'synthetic')
   n=Mock();n.reply_image.return_value={'data':{'message_id':'reply'}}
   msg={'chat_id':'chat','msg_type':'image','body':{'content':'{"image_key":"img_fake"}'}}
   n.get_message.side_effect=[RuntimeError('readback unavailable'),[msg]]
   with patch('agent.profit_summary.upload',return_value='img_fake'):
    with self.assertRaises(RuntimeError):profit_summary.deliver(n,'mid','chat',path)
    profit_summary.deliver(n,'mid','chat',path);profit_summary.deliver(n,'mid','chat',path)
   self.assertEqual(n.reply_image.call_count,1);self.assertEqual(n.get_message.call_count,2)
 def test_wrong_chat_readback_not_marked_verified(self):
  from agent import profit_summary
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'sample.png';path.write_bytes(b'synthetic')
   n=Mock();n.reply_image.return_value={'data':{'message_id':'reply'}}
   n.get_message.return_value=[{'chat_id':'other','msg_type':'image','body':{'content':'{"image_key":"img_fake"}'}}]
   with patch('agent.profit_summary.upload',return_value='img_fake'):
    with self.assertRaises(RuntimeError):profit_summary.deliver(n,'mid','chat',path)
   self.assertFalse(json.loads(path.with_suffix('.delivery.json').read_text()).get('verified'))
 def test_unknown_send_is_not_retried(self):
  from agent import profit_summary
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'sample.png';path.write_bytes(b'synthetic')
   n=Mock();n.reply_image.side_effect=TimeoutError()
   with patch('agent.profit_summary.upload',return_value='img_fake'):
    with self.assertRaises(TimeoutError):profit_summary.deliver(n,'mid','chat',path)
    with self.assertRaises(RuntimeError):profit_summary.deliver(n,'mid','chat',path)
   self.assertEqual(n.reply_image.call_count,1)

if __name__=='__main__':unittest.main()
