"""Synthetic midnight spend comparison, no production identifiers/network."""
import sys,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import product_spend_daily as s
CHINA=timezone(timedelta(hours=8))
class Tests(unittest.TestCase):
 def test_ranking_fixed_and_unmapped_not_other(self):
  data=[{'day':'2026-01-01','complete':True,'rows':[{'slot':0,'product':str(i),'name':'商品'+str(i),'amount':str(i)} for i in range(1,5)]+[{'slot':0,'product':None,'name':'','amount':'9'}]}, {'day':'2026-01-02','complete':False,'rows':[]}]
  v=s.build(data)
  self.assertEqual([p['name'] for p in v['products']],['商品4','商品3','商品2'])
  self.assertEqual(v['series'][0]['slots'][0],['4','3','2','1','9'])
  self.assertIsNone(v['series'][1]['total']);self.assertIsNone(v['series'][1]['slots'][0])
 def test_parse_rejects_duplicate_wrong_date_negative(self):
  r={'dimensions':{'ad_id':{'Value':'1'},'stat_time_day':{'ValueStr':'2026-01-01'},'stat_time_hour':{'ValueStr':'2026-01-01 00:00'}},'metrics':{'cost':{'Value':'1'}}}
  self.assertEqual(s.parse([r],'2026-01-01','cost',{})[0]['amount'],'1')
  with self.assertRaises(ValueError):s.parse([r,r],'2026-01-01','cost',{})
  with self.assertRaises(ValueError):s.parse([r],'2026-01-02','cost',{})
  r['metrics']['cost']['Value']='-1'
  with self.assertRaises(ValueError):s.parse([r],'2026-01-01','cost',{})
 def setup_engine(self,tmp):
  root=Path(tmp);(root/'config').mkdir();(root/'config/profit_daily.json').write_text('{}');(root/'config/rules.json').write_text('{"monitoring":{"enabled":true},"notify":{"roles":{"投手":["ou_synthetic"]}}}')
  e=Mock();e.root=root;e.env={'QC_ADVERTISER_IDS':'123','FEISHU_CHAT_ID':'oc_synthetic'}
  e.store.balances.return_value=[{'advertiser_id':'123','fetched_at':'2026-01-03T00:00:05+08:00'}]
  return e

 def test_missing_midnight_sample_or_daytime_no_send(self):
  with tempfile.TemporaryDirectory() as tmp:
   e=self.setup_engine(tmp)
   with patch.object(s,'prepare') as prep:
    self.assertFalse(s.tick(e,datetime(2026,1,3,12,tzinfo=CHINA)))
    e.store.balances.return_value=[]
    self.assertFalse(s.tick(e,datetime(2026,1,3,0,1,tzinfo=CHINA)));prep.assert_not_called()

 def test_collect_reconcile_failure_is_missing_not_zero(self):
  def pages(env,path,args,evidence,tag):
   if path==s.p.LIST:return [],[]
   if 'live-total' in tag:return [],[]
   metric=args['metrics'][0]
   if not args['dimensions']:return [{'metrics':{metric:{'Value':'5'}}}],[]
   return [],[]
  with tempfile.TemporaryDirectory() as tmp,patch.object(s.p,'pages',side_effect=pages):
   days=s.collect({'QC_ADVERTISER_IDS':'123'},['2026-01-01','2026-01-02'],Path(tmp))
   self.assertTrue(all(not d['complete'] for d in days))
   self.assertIsNone(s.build(days)['series'][0]['total']);self.assertIsNone(s.build(days)['unknown'])

if __name__=='__main__':unittest.main()
