"""Synthetic guide: exact dates, missing values, prioritization and frozen bundles."""
import sys,json,sqlite3,tempfile,unittest,importlib.util
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import hourly

def report(day='2026-01-02'):
 return {'date':day,'account':'123','policy':{'refund_rate':'.5'},'rows':[{'plan_id':'1','name':'测试商品','gmv':'100','spend':'40','profit':'-10'}], 'totals_calculable_only':{'gmv':'100','spend':'40','profit':'-10'},'calculable':1,'missing_profit':0,'as_of':day+'T10:00:00+08:00'}

class Tests(unittest.TestCase):
 def module(self):
  self.assertIsNotNone(importlib.util.find_spec('agent.daily_guide'),'Guide feature missing')
  from agent import daily_guide
  return daily_guide
 def test_missing_previous_is_unknown_not_zero(self):
  g=self.module();v=g.build(report(),None,'2026-01-03')
  self.assertIsNone(v['changes']['profit']);self.assertEqual(v['previous_date'],'2026-01-01');self.assertEqual(len(v['actions']),3)
 def test_matching_policy_and_plan_delta(self):
  g=self.module();old=report('2026-01-01');old['rows'][0]['profit']='20';old['totals_calculable_only']['profit']='20'
  v=g.build(report(),old,'2026-01-03');self.assertEqual(v['changes']['profit'],'-30.00');self.assertIn('测试商品',v['actions'][0]['title'])
 def test_wrong_date_account_policy_blocks_comparison(self):
  g=self.module()
  for field,value in [('date','2025-12-31'),('account','other'),('policy',{})]:
   old=report('2026-01-01');old[field]=value
   self.assertIsNone(g.build(report(),old,'2026-01-03')['changes']['profit'])
 def test_all_unknown_totals_remain_unknown(self):
  g=self.module();r=report();r['rows'][0].update(gmv=None,profit=None,spend=None);r['totals_calculable_only']=dict.fromkeys(['gmv','spend','profit']);r.update(calculable=0,missing_profit=1)
  v=g.build(r,None,'2026-01-03');self.assertIsNone(v['current']['profit']);self.assertIn('待核验',str(v['actions']))
 def test_prepend_preserve_and_refuse_existing_receipt(self):
  g=self.module()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'config').mkdir();(root/'config/profit_daily.json').write_text('{"render_python":"python","font":"font"}')
   out=root/'out';out.mkdir();(out/'result.json').write_text(json.dumps(report()))
   bundle={'directory':str(out),'date':'2026-01-02','account':'123','files':[{'kind':'file','path':'existing.xlsx'}],'actors':['ou_synthetic'],'text':'long summary'}
   with patch.object(g.subprocess,'run',side_effect=lambda args,**kw:Path(args[3]).write_bytes(b'png')):
    v=g.attach(root,{},datetime(2026,1,3,10,tzinfo=hourly.CHINA),bundle)
   self.assertEqual(v['files'][0]['kind'],'image');self.assertEqual(v['files'][1:],bundle['files']);self.assertEqual(v['actors'],bundle['actors']);self.assertNotIn('long summary',v['text']);self.assertIn('2026-01-02',v['text'])
   (out/'delivery.json').write_text('{}')
   with self.assertRaises(ValueError):g.attach(root,{},datetime(2026,1,3,10,tzinfo=hourly.CHINA),bundle)
 def test_prior_loader_uses_completed_ledger_not_directory_scan(self):
  g=self.module()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);db=root/'data/qianchuan/daily_brief.db';db.parent.mkdir(parents=True);out=root/'old';out.mkdir();(out/'result.json').write_text(json.dumps(report('2026-01-01')))
   with sqlite3.connect(db) as c:
    c.execute('create table jobs(account,day,payload,sent)');c.execute('insert into jobs values(?,?,?,?)',('123','2026-01-01',json.dumps({'type':'profit_bundle','directory':str(out),'account':'123','date':'2026-01-01'}),0))
   self.assertIsNone(g.previous(root,'123','2026-01-01'))
   with sqlite3.connect(db) as c:c.execute('update jobs set sent=1')
   self.assertEqual(g.previous(root,'123','2026-01-01')['date'],'2026-01-01')
if __name__=='__main__':unittest.main()
