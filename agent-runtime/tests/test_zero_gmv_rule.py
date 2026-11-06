"""Synthetic business imputation; explicit evidence, never applied to live plans."""
import unittest,sys,tempfile
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import plan_profit as p,profit_daily as d,data_completeness as c,report_preflight as gate
from test_plan_profit import POLICY
DAY='2026-01-01'
def zero(**kw):
 v=dict(plan_id='a',date=DAY,goal='VIDEO_PROM_GOODS',request_id='zero-proof',gmv_recheck={'date':DAY,'success':True,'gmv_missing':True,'request_ids':['recheck-proof']});v.update(kw);return v
class Tests(unittest.TestCase):
 def report(self,z=None,gmv=None,goal='VIDEO_PROM_GOODS'):
  return p.report([dict(plan_id='a',name='商品A',goal=goal)],[] if gmv is None else [dict(plan_id='a',date=DAY,gmv=gmv,spend='0')],[zero() if z is None else z],POLICY,date=DAY)
 def test_business_zero_has_provenance_and_preserves_unknown_raw(self):
  r=self.report();row=r['rows'][0]
  self.assertEqual((row['gmv'],row['profit']),('0.00','0.00'));self.assertEqual(row['gmv_source'],'business_rule_zero_spend');self.assertIsNone(row['raw_gmv']);self.assertIn('业务规则补值',row['status'])
  r.update(account='synthetic',policy=POLICY);v=c.build(r);self.assertEqual(v['business_imputed_plans'],1);self.assertIn('业务规则补值',v['sentence'])
 def test_missing_failed_wrong_day_recheck_and_live_never_impute(self):
  for proof in [None,{'date':DAY,'success':False,'gmv_missing':True,'request_ids':['x']},{'date':'2025-12-31','success':True,'gmv_missing':True,'request_ids':['x']},{'date':DAY,'success':True,'gmv_missing':True,'request_ids':[]}]:
   self.assertIsNone(self.report(zero(gmv_recheck=proof))['rows'][0]['gmv'])
  self.assertIsNone(self.report(goal='LIVE_PROM_GOODS')['rows'][0]['gmv'])
 def test_real_values_take_precedence(self):
  for v in ['0','100']:
   row=self.report(gmv=v)['rows'][0];self.assertEqual(row['gmv'],v+'.00');self.assertNotEqual(row.get('gmv_source'),'business_rule_zero_spend')
 def test_forged_imputation_blocks_preflight(self):
  r=self.report();r.update(account='synthetic',policy=POLICY);r['rows'][0]['evidence']['gmv_recheck']['success']=False
  errors=[];gate.inspect_report(r,'synthetic',DAY,errors,[],'main');self.assertIn('main:invalid_business_zero_evidence',errors)
 def test_collector_rechecks_only_zero_missing_and_records_success(self):
  def request(env,path,args):
   if path==d.LIST:
    rows=[{'ad_info':{'id':'a','name':'商品A'},'product_info':[]}] if args['marketing_goal']=='VIDEO_PROM_GOODS' and args['adlab_scene']=='OVERALL_PROJECT' else []
    return {'code':0,'request_id':'list-proof','data':{'ad_list':rows,'page_info':{'page':1,'total_page':bool(rows),'total_num':len(rows)}}}
   return {'code':0,'request_id':'report-proof','data':{'rows':[],'page_info':{'page':1,'total_page':0,'total_number':0}}}
  with tempfile.TemporaryDirectory() as tmp:r=d.collect({'QC_ADVERTISER_IDS':'123'},DAY,date(2026,1,2),Path(tmp),request)
  self.assertTrue(r['no_spend'][0]['gmv_recheck']['success'])
 def test_old_coverage_structure_is_preserved(self):
  from agent import daily_guide
  from test_daily_guide import report
  r=report();view=daily_guide.build(r,None,'2026-01-03',include_coverage=True,coverage_version=1)
  self.assertNotIn('business_imputed_plans',view['coverage']);self.assertNotIn('gmv_source',view['coverage']['plans'][0])
 def test_zero_evidence_wrong_day_rejected(self):
  with self.assertRaises(ValueError):self.report(zero(date='2025-12-31'))
 def test_business_marker_requires_excel_label(self):
  from unittest.mock import patch
  from test_report_preflight import book
  r=self.report();r.update(account='123',policy=POLICY)
  # Other workbook policy mismatches are independent; missing marker must be explicitly caught.
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'test.xlsx';book(path,r);errors=[];gate.inspect_excel(path,r,errors)
   self.assertIn('excel:business_zero_label_7',errors)
if __name__=='__main__':unittest.main()
