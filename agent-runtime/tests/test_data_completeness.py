"""Synthetic coverage evidence; never infer zero or full-account completeness."""
import unittest,tempfile,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import daily_guide,report_preflight
from test_report_preflight import fixture

class Tests(unittest.TestCase):
 def build(self,*args):
  from agent import data_completeness
  return data_completeness.build(*args)
 def report(self):
  return {'date':'2026-01-01','account':'123','rows':[
   {'plan_id':'1','name':'商品A','gmv':'100','spend':'0','profit':'50'},
   {'plan_id':'2','name':'全店托管','gmv':None,'spend':'0','profit':None},
   {'plan_id':'3','name':'其他计划','gmv':'20','spend':None,'profit':None}],
   'policy':{},'as_of':'2026-01-02T10:00:00+08:00'}
 def test_zero_missing_mapping_and_scope(self):
  r=self.report();day={'day':r['date'],'account':'123','rows':[{'plan':'1','product':'synthetic-product','name':'商品A'},{'plan':'3','product':None,'name':''}]}
  v=self.build(r,day)
  self.assertEqual((v['total_plans'],v['calculable_plans'],v['pending_plans']),(3,1,2))
  self.assertEqual(v['missing_fields'],{'gmv':1,'spend':1,'profit':2})
  self.assertEqual(v['known_zero_spend_plans'],2)
  self.assertEqual(v['product_mapping'],{'linked':1,'managed':1,'unlinked':1,'pending':0})
  self.assertEqual(v['calculable_percent'],'33.33');self.assertIsNone(v['account_coverage_percent'])
  self.assertIn('待核验',v['sentence']);self.assertIn('本次报表',v['sentence'])
 def test_empty_and_absent_mapping_are_unknown(self):
  r=self.report();v=self.build(r)
  self.assertEqual(v['product_mapping']['pending'],2)
  r['rows']=[];v=self.build(r)
  self.assertIsNone(v['calculable_percent']);self.assertIn('待核验',v['sentence'])
 def test_duplicate_or_wrong_mapping_scope_rejected(self):
  r=self.report();r['rows'].append(dict(r['rows'][0]))
  with self.assertRaises(ValueError):self.build(r)
  with self.assertRaises(ValueError):self.build(self.report(),{'day':'2025-12-31','account':'123','rows':[]})
 def test_preflight_local_dashboard_and_guide_binding(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);b=fixture(root);main=json.loads((root/'result.json').read_text());days=json.loads((root/'two_day/collected.json').read_text())
   guide=daily_guide.build(main,None,'2026-01-02',coverage_day=days[-1],include_coverage=True)
   self.assertEqual(guide['coverage']['total_plans'],3)
   (root/'guide/guide.json').write_text(json.dumps(guide));b=report_preflight.seal(b)
   result=report_preflight.check(b)
   self.assertEqual(result['coverage'],guide['coverage'])
   self.assertIn('数据完整性', (root/'preflight.txt').read_text())
   guide['coverage']['total_plans']=999;(root/'guide/guide.json').write_text(json.dumps(guide));b=report_preflight.seal(b)
   with self.assertRaises(report_preflight.PreflightBlocked):report_preflight.check(b)
 def test_legacy_guide_bundle_remains_valid(self):
  with tempfile.TemporaryDirectory() as tmp:
   b=fixture(Path(tmp));result=report_preflight.check(report_preflight.seal(b))
   self.assertIn('coverage',result);self.assertTrue(result['allowed'])
if __name__=='__main__':unittest.main()
