"""Synthetic revision evidence and readable notices; no network or production data."""
import sys,json,tempfile,unittest
from pathlib import Path
from datetime import timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import analysis_gate as g,monitor as m
from test_analysis_gate import NOW,snapshot,row

class Tests(unittest.TestCase):
 def values(self):
  rows=[row(gmv='1000',orders='4') for _ in range(3)]
  for r,v in zip(rows,['900','800','550']):r['backend_net']=v
  return [snapshot(NOW-timedelta(minutes=60-30*i),[r]) for i,r in enumerate(rows)]
 def test_preserve_each_decrease_with_time_and_values(self):
  result=g.assess(self.values(),'999','plan',NOW)
  changes=result['entities']['123']['revisions']
  self.assertEqual([(c['before'],c['after'],c['decrease']) for c in changes],[('900','800','100'),('800','550','250')])
  self.assertEqual(changes[0]['start'],self.values()[0]['request_end'])
  self.assertEqual(changes[1]['end'],self.values()[2]['request_end'])
  self.assertFalse(g.usable(result['entities']['123'],'backend_net'))
  self.assertTrue(g.usable(result['entities']['123'],'gmv','ad_spend'))
 def test_card_identifies_plan_metric_amounts_and_does_not_repeat(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=m.Store(tmp);result=g.assess(self.values(),'999','plan',NOW)
   g.record(store,result,NOW);g.record(store,result,NOW+timedelta(seconds=1))
   messages=store.pending('999');self.assertEqual(len(messages),1)
   text=messages[0][1]
   for word in ['测试计划123','123','后台净成交金额','900.00元 → 800.00元','减少100.00元','减少250.00元','14:30—15:00','15:00—15:30','退款提示（按净成交减少识别）']:
    self.assertIn(word,text)
   self.assertNotIn('疑似退款',text);self.assertNotIn('完整可比对象',text)
 def test_mixed_revision_notice_not_retired_as_routine(self):
  values=self.values();values[-1]['rows'].append(row(pid='456'))
  result=g.assess(values,'999','plan',NOW)
  with tempfile.TemporaryDirectory() as tmp:
   store=m.Store(tmp);g.record(store,result,NOW)
   g.record(store,result,NOW,notify_observation=False)
   self.assertEqual(len(store.pending('999')),1)
 def test_order_decrease_is_orders_not_currency(self):
  values=self.values()
  for r,v in zip(values,['4','3','3']):r['rows'][0]['order_count']=v
  result=g.assess(values,'999','plan',NOW)
  text='\n'.join(g.revision_details(result))
  self.assertIn('订单数：4单 → 3单，减少1单',text)
 def test_spend_revision_is_not_called_refund(self):
  values=self.values()
  for v,spend in zip(values,['200','100','100']):
   v['rows'][0]['backend_net']='900';v['rows'][0]['ad_spend']=spend
  result=g.assess(values,'999','plan',NOW)
  text=str(g.notice_elements(result,NOW))
  self.assertNotIn('退款提示',text)
  self.assertNotIn('原因',text)
 def test_missing_values_do_not_become_zero_decreases(self):
  values=self.values();del values[1]['rows'][0]['backend_net']
  result=g.assess(values,'999','plan',NOW)
  self.assertEqual(result['entities']['123']['revisions'],[])
  self.assertEqual(g.revision_details(result),[])

if __name__=='__main__':unittest.main()
