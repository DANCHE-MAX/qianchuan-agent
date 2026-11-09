import unittest,copy
from agent import refund_differences as f
class Tests(unittest.TestCase):
 def setUp(self):
  self.report={'account':'demo','date':'2026-01-01','rows':[{'plan_id':'p','gmv':'100','spend':'10'}]}
  self.day={'account':'demo','day':'2026-01-01','complete':False,'rows':[{'plan':'p','product':'a','name':'商品','gmv':'120','amount':'10'}],'warnings':['与原日报采集结果不一致，待核验']}
  self.record={'id':'demo-confirmation','status':'business_confirmed','kind':'refund_basis_difference','account':'demo','date':'2026-01-01','plan_id':'p','metric':'gmv','original':'100','comparison':'120','amount':'20'}
 def test_exact_confirmation_only_and_no_arithmetic_changes(self):
  before=copy.deepcopy(self.day);self.assertEqual(f.covered(self.day,self.report,[self.record]),['demo-confirmation']);self.assertEqual(self.day,before)
  for key,value in [('account','other'),('date','2026-01-02'),('plan_id','other'),('metric','spend'),('amount','21'),('original','120'),('status','pending')]:
   r=dict(self.record,**{key:value});self.assertFalse(f.covered(self.day,self.report,[r]),key)
 def test_unknown_spend_or_extra_difference_still_blocks(self):
  self.day['rows'][0]['amount']='11';self.assertFalse(f.covered(self.day,self.report,[self.record]))
  self.day['rows'][0]['amount']='10';self.report['rows'][0]['gmv']=None;self.assertFalse(f.covered(self.day,self.report,[self.record]))
 def test_applied_warning_preserves_pending_and_amounts(self):
  f.annotate(self.day,self.report,[self.record]);self.assertFalse(self.day['complete']);self.assertEqual(self.day['rows'][0]['gmv'],'120');self.assertNotIn('与原日报采集结果不一致，待核验',self.day['warnings'])
  self.assertTrue(any('业务确认' in x for x in self.day['warnings']))
 def test_preflight_only_passes_exact_confirmed_mismatch(self):
  import tempfile,json
  from pathlib import Path
  from unittest.mock import patch
  from test_report_preflight import fixture,POLICY
  from agent import report_preflight as gate,product_spend_daily as spend,two_day_daily as two
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);bundle=fixture(root);days=json.loads((root/'two_day/collected.json').read_text());report=json.loads((root/'result.json').read_text())
   day=days[-1];day['rows'][0]['gmv']='120';day['complete']=False;day['warnings'].append(f.CONFLICT)
   for name,value in [('collected',days),('stack',spend.build(days)),('pies',two.pie_view(days,POLICY))]:(root/('two_day/'+name+'.json')).write_text(json.dumps(value))
   bundle=gate.seal(bundle);confirmation=dict(self.record,account='123',plan_id='1')
   with patch.object(f,'records',return_value=[]):
    with self.assertRaises(gate.PreflightBlocked):gate.check(bundle)
   with patch.object(f,'records',return_value=[confirmation]):
    result=gate.check(bundle);self.assertEqual(result['status'],'PASS_WITH_MISSING')
   with patch.object(f,'records',return_value=[dict(confirmation,amount='21')]):
    with self.assertRaises(gate.PreflightBlocked):gate.check(bundle)
 def test_confirmed_difference_uses_daily_profit_but_keeps_raw_gmv(self):
  self.day.update(inventory_complete=True);self.report['rows'][0]['profit']='15.25'
  f.annotate(self.day,self.report,[self.record])
  self.assertTrue(self.day['spend_verified']);self.assertTrue(self.day['profit_verified'])
  self.assertEqual(self.day['daily_profit_by_plan'],{'p':'15.25'});self.assertEqual(self.day['rows'][0]['gmv'],'120');self.assertFalse(self.day['complete'])
 def test_missing_collection_cannot_be_unblocked(self):
  self.day.update(inventory_complete=True);self.day['warnings'].append('小时采集失败')
  f.annotate(self.day,self.report,[self.record]);self.assertFalse(self.day.get('spend_verified',False))
 def test_scoped_charts_preserve_daily_profit_and_spend(self):
  from agent import two_day_daily as two,product_spend_daily as spend
  self.day.update(inventory_complete=True,inventory=['p'],report_missing=1);self.day['rows'][0]['slot']=0
  self.report['rows'][0]['profit']='15.25';f.annotate(self.day,self.report,[self.record])
  chart=spend.build([self.day]);pie=two.pie_view([self.day],{})['days'][0]
  self.assertEqual(chart['series'][0]['total'],'10');self.assertEqual(pie['positive_total'],'15.25');self.assertEqual(pie['ad_total'],'10');self.assertEqual(pie['missing_plans'],1)
  self.assertEqual(self.day['rows'][0]['gmv'],'120')
 def test_scope_flags_require_confirmation_in_preflight(self):
  import tempfile,json
  from pathlib import Path
  from test_report_preflight import fixture
  from agent import report_preflight as gate
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);bundle=fixture(root);p=root/'two_day/collected.json';days=json.loads(p.read_text());days[0]['spend_verified']=True;p.write_text(json.dumps(days));bundle=gate.seal(bundle)
   with self.assertRaises(gate.PreflightBlocked):gate.check(bundle)
 def test_new_scoped_projection_passes_gate_and_tamper_blocks(self):
  import tempfile,json
  from pathlib import Path
  from unittest.mock import patch
  from test_report_preflight import fixture,POLICY
  from agent import report_preflight as gate,product_spend_daily as spend,two_day_daily as two
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);bundle=fixture(root);days=json.loads((root/'two_day/collected.json').read_text());report=json.loads((root/'result.json').read_text())
   day=days[-1];day['rows'][0]['gmv']='120';day['complete']=False;day['inventory_complete']=True;day['warnings'].append(f.CONFLICT)
   confirmation=dict(self.record,account='123',plan_id='1');f.annotate(day,report,[confirmation]);self.assertTrue(day['spend_verified'])
   for name,value in [('collected',days),('stack',spend.build(days)),('pies',two.pie_view(days,POLICY))]:(root/('two_day/'+name+'.json')).write_text(json.dumps(value))
   bundle=gate.seal(bundle)
   with patch.object(f,'records',return_value=[confirmation]):
    self.assertEqual(gate.check(bundle)['status'],'PASS_WITH_MISSING')
    day['daily_profit_by_plan']['1']='999';(root/'two_day/collected.json').write_text(json.dumps(days));bundle=gate.seal(bundle)
    with self.assertRaises(gate.PreflightBlocked):gate.check(bundle)
