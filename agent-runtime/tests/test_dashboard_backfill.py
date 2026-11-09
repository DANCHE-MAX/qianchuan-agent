import hashlib,json,tempfile,unittest
from pathlib import Path
from datetime import date
from unittest.mock import Mock
from agent import dashboard_backfill,profit_daily
class BackfillTests(unittest.TestCase):
 def test_bound_local_report_not_delivery(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);folder=root/'out/demo';folder.mkdir(parents=True);p=folder/'result.json';p.write_text(json.dumps({'account':'demo','date':'2026-01-01','rows':[]}))
   index=root/'data/dashboard_backfills/demo/2026-01-01.json';index.parent.mkdir(parents=True);index.write_text(json.dumps({'account':'demo','date':'2026-01-01','status':'collected_unsent','scope':'product_only','directory':'out/demo','files':{'out/demo/result.json':hashlib.sha256(p.read_bytes()).hexdigest()}}))
   self.assertEqual(dashboard_backfill.load(root,'demo','2026-01-01')['_backfill_status'],'历史补采测算·未发送')
   p.write_text('{}')
   with self.assertRaises(ValueError):dashboard_backfill.load(root,'demo','2026-01-01')
 def test_history_rejects_incomplete_day(self):
  req=Mock()
  with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):profit_daily.collect({},'2026-01-09',date(2026,1,9),Path(tmp),request=req,historical_products=True)
  req.assert_not_called()
 def test_history_requests_no_live(self):
  calls=[]
  def req(env,path,args):
   calls.append(args)
   if path==profit_daily.LIST:
    rows=[{'ad_info':{'id':'demo','name':'sample'},'product_info':[]}] if args['adlab_scene']=='OVERALL_PROJECT' else []
    return {'code':0,'data':{'ad_list':rows,'page_info':{'page':1,'total_page':1 if rows else 0,'total_num':len(rows)}}}
   return {'code':0,'data':{'rows':[],'page_info':{'page':1,'total_page':0,'total_number':0}}}
  with tempfile.TemporaryDirectory() as tmp:profit_daily.collect({'QC_ADVERTISER_IDS':'123'},'2026-01-01',date(2026,1,9),Path(tmp),request=req,historical_products=True)
  self.assertTrue(all(x.get('marketing_goal')!='LIVE_PROM_GOODS' and 'LIVE' not in x.get('data_topic','') for x in calls))
