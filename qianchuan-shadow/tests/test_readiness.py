import unittest
from qc_shadow.readiness import budget_events, product_fact, budget_fact
class ReadinessTests(unittest.TestCase):
 def test_creation_and_change_are_distinct(self):
  rows=[{'object_id':1,'create_time':'2026-09-01 10:00:00','content_title':'新建','content_log':['预算: 日预算500.0元'],'log_id':'a'}, {'object_id':1,'create_time':'2026-09-02 10:00:00','content_title':'修改','content_log':['修改 预算: 日预算500元 -> 日预算300元'],'log_id':'b'}, {'object_id':2,'content_log':['预算: 日预算999元']}]
  result=budget_events(rows,'1');self.assertEqual([r['after'] for r in result],['500.0','300']);self.assertEqual(result[1]['before'],'500')
 def test_current_never_freezes_baseline(self):
  fact=budget_fact({'budget':5000,'budget_mode':'BUDGET_MODE_DAY'})
  self.assertEqual(fact['current'],'5000');self.assertIsNone(fact['formal_B']);self.assertFalse(fact['execution_allowed'])
 def test_invalid_budget_unknown(self):
  self.assertIsNone(budget_fact({'budget':float('nan')})['current'])
 def test_product_not_promoted_and_wrong_id_rejected(self):
  response={'code':0,'data':{'product_list':[{'id':1,'discount_lower_price':199,'discount_higher_price':199}]}}
  self.assertFalse(product_fact(response,'1')['refund_verified']);self.assertEqual(product_fact(response,'2')['status'],'missing')
 def test_api_failure_not_zero(self):
  self.assertEqual(product_fact({'code':40100},'1')['status'],'api_failed')
 def test_changing_page_totals_never_complete(self):
  from qc_shadow.readiness import pagination_audit
  pages=[{'code':0,'data':{'page_info':{'page':i,'total_page':3,'total_number':n},'logs':[{'log_id':'shared'}]}} for i,n in enumerate([51,56,60],1)]
  self.assertFalse(pagination_audit(pages)['completeness_verified'])
 def test_conditional_refund_not_verified(self):
  from qc_shadow.readiness import conditional_daily
  r=conditional_daily([], '2026-11-02T10:00:00+08:00',{'rate':'0.6','verified':False})
  self.assertFalse(r['refund_verified']);self.assertTrue(r['refund_assumed_for_simulation'])
 def test_loaded_evidence_in_manifest(self):
  from qc_shadow.readiness import load_evidence
  from tempfile import TemporaryDirectory
  from pathlib import Path
  import hashlib
  with TemporaryDirectory() as d:
   p=Path(d)/'shadow.json';p.write_bytes(b'{"daily":[]}');manifest={}
   self.assertEqual(load_evidence(p,manifest),{'daily':[]})
   self.assertEqual(manifest[str(p)],hashlib.sha256(p.read_bytes()).hexdigest())
