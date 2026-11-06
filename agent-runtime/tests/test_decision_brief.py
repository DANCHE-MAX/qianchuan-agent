"""Synthetic decisions: coarse model, source scope, no execution."""
import sys,json,tempfile,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import daily_guide,profit_analysis,hourly,monitor

def report():
 return dict(date='2026-01-02',account='123',policy={},calculable=3,missing_profit=1,totals_calculable_only=dict(gmv='100',spend='40',profit='8'),rows=[
  dict(plan_id='1',name='样例甲',profit='20',spend='10'),dict(plan_id='2',name='样例乙',profit='-12',spend='25'),
  dict(plan_id='3',name='全店托管',profit='0',spend='5'),dict(plan_id='4',name='未知',profit=None,spend=None)])
class Decisions(unittest.TestCase):
 def test_priority_and_missing(self):
  from agent import decision_brief as d
  v=d.rank(report());self.assertEqual(v['profit'][0]['name'],'样例甲（计划）');self.assertEqual(v['loss'][0]['profit'],'-12.00');self.assertEqual(v['spend'][0]['spend'],'25.00');self.assertEqual(v['pending'],1)
 def test_mapping_aggregate_not_names_and_partial_excluded(self):
  from agent import decision_brief as d
  r=report();m=dict(day=r['date'],account=r['account'],rows=[dict(plan='1',product='p',name='样例商品'),dict(plan='2',product='p',name='样例商品')])
  self.assertEqual(d.rank(r,m)['profit'][0]['profit'],'8.00')
  r['rows'][1]['profit']=None
  self.assertEqual(d.rank(r,m)['profit'],[])
  m['day']='2026-01-01'
  with self.assertRaises(ValueError):d.rank(r,m)
 def test_guide_version_preserves_legacy(self):
  v=daily_guide.build(report(),None,'2026-01-03',decision_version=1)
  self.assertEqual(v['decision_version'],1);self.assertIn('优先关注',v['actions'][0]['title']);self.assertNotIn('decision_version',daily_guide.build(report(),None,'2026-01-03'))
 def test_qa_no_llm_even_disabled(self):
  kb=Mock(last_context='')
  with patch.object(profit_analysis,'target_day',return_value='2026-01-02'),patch.object(profit_analysis,'load',return_value=report()):
   ans=profit_analysis.answer('昨天哪个商品赚得最多','/unused',{},kb)
  self.assertIn('样例甲',ans);self.assertIn('粗略',ans);kb.ask.assert_not_called()
 def test_qa_scope_and_unsupported(self):
  from agent import decision_brief as d
  self.assertIsNone(d.question('这周总结'));self.assertEqual(d.question('昨天广告费花在哪里'),'spend')
  r=report()
  for row in r['rows']:row['profit']=None
  self.assertIn('待核验',d.answer('昨天谁赚得最多',r))
 def test_merge_durable_no_duplicates(self):
  from agent import alert_digest as d
  with tempfile.TemporaryDirectory() as tmp:
   store=monitor.Store(tmp);now=datetime(2026,1,2,12,1,tzinfo=hourly.CHINA)
   for pid in ('1','2'):
    payload=monitor.card('异常',[]);payload['_brief_alert']={'kind':'plan','window':'11:30—12:00','text':'样例'+pid+'：消耗上升'}
    store.enqueue('anomaly26:123:plan:'+pid+':window','123',now,payload)
   notify=Mock();d.deliver(store,'123','synthetic-chat',now,notify,lambda key:True)
   d.deliver(store,'123','synthetic-chat',now,notify,lambda key:True)
   self.assertEqual(notify.send_card.call_count,1);self.assertEqual(store.pending('123'),[])
 def test_retry_frozen_members_same_uuid_and_admission(self):
  from agent import alert_digest as d
  with tempfile.TemporaryDirectory() as tmp:
   store=monitor.Store(tmp);now=datetime(2026,1,2,12,1,tzinfo=hourly.CHINA)
   for pid in ('1','2'):
    p=monitor.card('异常',[]);p['_brief_alert']={'kind':'plan','window':'same','text':'样例'+pid};store.enqueue('anomaly26:123:plan:'+pid,'123',now,p)
   n=Mock();n.send_card.side_effect=RuntimeError('transport')
   with self.assertRaises(RuntimeError):d.deliver(store,'123','synthetic-chat',now,n,lambda k:True)
   first=n.send_card.call_args.kwargs['request_uuid'];n.reset_mock();n.send_card.side_effect=None
   d.deliver(store,'123','synthetic-chat',now,n,lambda k:False);n.send_card.assert_not_called()
   d.deliver(store,'123','synthetic-chat',now,n,lambda k:True)
   self.assertEqual(n.send_card.call_args.kwargs['request_uuid'],first)
 def test_separate_windows_mentions_and_expiry(self):
  from agent import alert_digest as d
  with tempfile.TemporaryDirectory() as tmp:
   store=monitor.Store(tmp);now=datetime(2026,1,2,12,1,tzinfo=hourly.CHINA)
   mention={'tag':'div','text':{'tag':'lark_md','content':'<at id="synthetic_actor"></at>'}}
   for pid,window in [('1','a'),('2','a'),('3','b')]:
    p=monitor.card('异常',[mention]);p['_brief_alert']={'kind':'plan','window':window,'text':'样例'+pid};store.enqueue('anomaly26:123:plan:'+pid,'123',now,p)
   n=Mock();d.deliver(store,'123','synthetic-chat',now,n,lambda k:True)
   self.assertEqual(n.send_card.call_count,2)
   for call in n.send_card.call_args_list:self.assertEqual(str(call.args[0]).count('synthetic_actor'),1)
 def test_qa_missing_date_never_calls_model(self):
  kb=Mock(last_context='')
  with patch.object(profit_analysis,'load',side_effect=ValueError('missing')):
   value=profit_analysis.answer('昨天哪个商品赚得最多','/unused',{},kb)
  self.assertIn('未使用旧日报',value);kb.ask.assert_not_called()
if __name__=='__main__':unittest.main()
