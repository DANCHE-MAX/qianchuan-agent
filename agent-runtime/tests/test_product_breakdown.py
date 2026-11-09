import unittest,tempfile,json
from pathlib import Path
from copy import deepcopy
from agent import product_breakdown as b
class BreakdownTests(unittest.TestCase):
 def report(self):return {'account':'a','date':'2026-01-01','rows':[{'plan_id':'p','name':'多商品计划','gmv':'0','spend':'5','profit':'-5'}],'totals_calculable_only':{'gmv':'0','spend':'5','profit':'-5'}}
 def products(self):return [{'id':'g','name':'商品甲'},{'id':'h','name':'商品乙'}]
 def test_no_double_count_and_missing_not_zero(self):
  r=self.report();old=deepcopy(r);v=b.view(r,'p',self.products(),[{'date':r['date'],'product_id':'g','gmv':'0','spend':'5'}])
  self.assertEqual(r,old);self.assertEqual(v['missing'],1);self.assertIsNone(v['products'][1]['gmv']);self.assertFalse(v['included_in_totals']);self.assertEqual(v['status'],'partial');self.assertEqual(v['known_spend'],'5.00');b.validate(r,[v])
 def test_duplicate_wrong_scope_and_negative_rejected(self):
  row={'date':'2026-01-01','product_id':'g','gmv':'0','spend':'5'}
  for rows in [[row,row],[dict(row,date='2026-01-02')],[dict(row,product_id='other')],[dict(row,spend='-1')]]:
   with self.assertRaises(ValueError):b.view(self.report(),'p',self.products(),rows)
 def test_mismatch_cannot_pass_preflight(self):
  r=self.report();v=b.view(r,'p',self.products(),[{'date':r['date'],'product_id':'g','gmv':'0','spend':'6'}]);self.assertEqual(v['status'],'mismatch')
  with self.assertRaises(ValueError):b.validate(r,[v])
 def test_unknown_plan_amount_not_confirmed(self):
  r=self.report();r['rows'][0]['spend']=None;v=b.view(r,'p',self.products(),[]);self.assertEqual(v['status'],'pending');self.assertIsNone(v['plan_spend'])
 def test_complete_does_not_change_plan_profit(self):
  r=self.report();v=b.view(r,'p',self.products(),[{'date':r['date'],'product_id':'g','gmv':'0','spend':'2'},{'date':r['date'],'product_id':'h','gmv':'0','spend':'3'}]);self.assertEqual(v['status'],'complete');self.assertEqual(r['rows'][0]['profit'],'-5')
 def test_capture_failure_never_reuses_historical(self):
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'data/product_identity').mkdir(parents=True);e=root/'source';e.mkdir()
   (root/'data/product_identity/pending.json').write_text(json.dumps([dict(plan_id='p',name='p',products=self.products())]))
   r=self.report();old=deepcopy(r['rows']);net=Mock(side_effect=AssertionError('No request without dated bindings'))
   b.capture(root,{},r,e,net)
   self.assertEqual(r['rows'],old);self.assertEqual(r['product_breakdowns'][0]['status'],'pending');net.assert_not_called()
 def test_capture_real_rows_and_pagination(self):
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'data/product_identity').mkdir(parents=True);e=root/'source';e.mkdir();r=self.report()
   (root/'data/product_identity/pending.json').write_text(json.dumps([dict(plan_id='p',name='p',products=self.products())]))
   args=dict(advertiser_id='a',start_time=r['date']+' 00:00:00',end_time=r['date']+' 23:59:59')
   (e/'VIDEO_PROM_GOODS-test.json').write_text(json.dumps(dict(request=args,response={'code':0,'data':{'ad_list':[{'ad_info':{'id':'p'},'product_info':[dict(product_id=x['id'],product_name=x['name']) for x in self.products()]}]}})))
   row=dict(dimensions={'ad_id':{'Value':'p'},'qianchuan_product_id':{'Value':'g'},'stat_time_day':{'ValueStr':r['date']}},metrics={b.GMV:{'Value':0},'stat_cost_for_roi2':{'Value':5}})
   net=Mock(return_value=dict(code=0,data=dict(rows=[row],page_info=dict(page=1,total_number=1,total_page=1))))
   b.capture(root,{},r,e,net);v=r['product_breakdowns'][0];self.assertEqual(v['missing'],1);b.validate(r,[v]);self.assertEqual(net.call_count,1)
 def test_mutated_totals_rejected(self):
  r=self.report();v=b.view(r,'p',self.products(),[]);v['included_in_totals']=True
  with self.assertRaises(ValueError):b.validate(r,[v])
