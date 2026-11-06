"""Synthetic delayed evidence; no live network, sender or production data."""
import unittest,tempfile,json,sys,hashlib,sqlite3
from pathlib import Path
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import hourly,plan_profit
from test_plan_profit import POLICY
NOW=datetime(2026,1,3,10,10,tzinfo=hourly.CHINA)
def report(imputed=True):
 row=dict(plan_id='1',name='测试商品',goal='VIDEO_PROM_GOODS',scene='OVERALL_PROJECT',gmv='0.00' if imputed else None,spend='0.00',raw_gmv=None,gmv_source='business_rule_zero_spend' if imputed else 'missing')
 row.update(plan_profit.calculate(row['gmv'],row['spend'],POLICY))
 return {'account':'123','date':'2026-01-01','policy':POLICY,'rows':[row]}
def response(args,gmv='100',spend='5',day='2026-01-01',pid='1'):
 rows=[] if gmv is None else [{'dimensions':{'ad_id':{'Value':pid},'stat_time_day':{'ValueStr':day}},'metrics':{'total_pay_order_gmv_include_coupon_for_roi2':{'Value':gmv},'stat_cost_for_roi2':{'Value':spend}}}]
 return {'code':0,'request_id':'synthetic-proof','data':{'rows':rows,'page_info':{'page':1,'total_page':1 if rows else 0,'total_number':len(rows)}}}
class Tests(unittest.TestCase):
 def store(self,root):
  from agent.verification_tracker import Store
  return Store(Path(root))
 def test_idempotent_ingest_compare_and_reopen(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=self.store(tmp);r=report();before=json.dumps(r,sort_keys=True)
   s.ingest(r,'source-one',NOW);s.ingest(r,'source-one',NOW)
   calls=[]
   def req(env,path,args):calls.append(args);return response(args)
   s.check({'QC_ADVERTISER_IDS':'123'},NOW,req)
   s.check({'QC_ADVERTISER_IDS':'123'},NOW,req)
   v=self.store(tmp).snapshot('123');self.assertEqual(len(v['cases']),1);c=v['cases'][0]
   self.assertEqual(c['status'],'resolved');self.assertEqual(c['attempts'],1);self.assertEqual(len(calls),1)
   self.assertEqual(c['comparison']['original_gmv'],'0.00');self.assertEqual(c['comparison']['observed_gmv'],'100.00')
   self.assertEqual(c['comparison']['profit_difference'],'17.26');self.assertTrue(c['comparison']['business_imputed'])
   self.assertEqual(json.dumps(r,sort_keys=True),before)
 def test_absent_or_failed_stays_pending_then_real_closes(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=self.store(tmp);s.ingest(report(False),'one',NOW)
   s.check({'QC_ADVERTISER_IDS':'123'},NOW,lambda e,p,a:response(a,None))
   c=s.snapshot('123')['cases'][0];self.assertEqual(c['status'],'pending');self.assertIsNone(c['comparison'])
   s.check({'QC_ADVERTISER_IDS':'123'},NOW+timedelta(days=1),lambda e,p,a:response(a))
   c=s.snapshot('123')['cases'][0];self.assertEqual(c['attempts'],2);self.assertEqual(c['status'],'resolved');self.assertIsNone(c['comparison']['profit_difference'])
 def test_wrong_date_pid_and_partial_money_cannot_close(self):
  for kwargs in [{'day':'2026-01-02'},{'pid':'other'},{'spend':None}]:
   with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as tmp:
    s=self.store(tmp);s.ingest(report(),'one',NOW);s.check({'QC_ADVERTISER_IDS':'123'},NOW,lambda e,p,a:response(a,**kwargs))
    self.assertNotEqual(s.snapshot('123')['cases'][0]['status'],'resolved')
 def test_live_old_mapping_only_and_account_are_bounded(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=self.store(tmp);r=report(False);r['rows'][0]['goal']='LIVE_PROM_GOODS';s.ingest(r,'live',NOW)
   r=report(False);r['date']='2025-12-01';s.ingest(r,'old',NOW)
   r=report();r['rows'][0]['gmv_source']='api';s.ingest(r,'mapping',NOW,{'1':'pending'})
   def req(*a):raise AssertionError('No supported monetary query')
   s.check({'QC_ADVERTISER_IDS':'123'},NOW,req)
   self.assertTrue(all(c['status']=='manual_pending' for c in s.snapshot('123')['cases']))
   self.assertEqual(s.snapshot('456')['cases'],[])
 def test_exception_is_local_and_does_not_close(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=self.store(tmp);s.ingest(report(),'one',NOW)
   def req(*a):raise TimeoutError('secret must not be copied')
   s.check({'QC_ADVERTISER_IDS':'123'},NOW,req)
   c=s.snapshot('123')['cases'][0];self.assertEqual(c['status'],'pending');self.assertIn('TimeoutError',c['reason']);self.assertNotIn('secret',c['reason'])
 def test_same_plan_new_source_keeps_original_version(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=self.store(tmp);s.ingest(report(),'one',NOW);s.ingest(report(False),'two',NOW)
   self.assertEqual(len(s.snapshot('123')['cases']),2)
 def test_completed_job_hook_only_after_sent_no_new_send(self):
  from agent import daily_brief
  from unittest.mock import Mock,patch
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'config').mkdir();(root/'config/daily_brief.json').write_text('{"enabled":true,"profit_report":true}')
   db=root/'data/qianchuan/daily_brief.db';db.parent.mkdir(parents=True)
   with sqlite3.connect(db) as c:
    c.execute('CREATE TABLE jobs(account,day,chat,payload,sent,attempts,retry_at,error_notice,last_error,PRIMARY KEY(account,day))')
    c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',('123','2026-01-02','synthetic-chat','{}',1,0,None,0,None))
   factory=Mock();env={'QC_ADVERTISER_IDS':'123','FEISHU_CHAT_ID':'synthetic-chat'}
   with patch('agent.verification_tracker.safe_tick') as tracker:
    self.assertFalse(daily_brief.Engine(root,env,factory).tick(NOW));tracker.assert_called_once()
   factory.assert_not_called()
 def test_safe_tick_once_per_day_and_no_history_mutation(self):
  from agent import verification_tracker as v
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'data/qianchuan').mkdir(parents=True)
   original=root/'original.json';original.write_text(json.dumps(report()));before=original.read_bytes()
   with patch.object(v,'ingest_sent',return_value=[]) as ingest,patch.object(v.Store,'check') as check:
    v.safe_tick(root,{'QC_ADVERTISER_IDS':'123'},NOW);v.safe_tick(root,{'QC_ADVERTISER_IDS':'123'},NOW)
    ingest.assert_called_once();check.assert_called_once()
   self.assertEqual(original.read_bytes(),before)
 def test_source_loader_requires_receipt_and_scoped_hash(self):
  from agent import verification_tracker as v
  from test_report_preflight import fixture
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);folder=root/'out/profit_daily/synthetic/2026-01-01/version';folder.mkdir(parents=True)
   bundle=fixture(folder);db=root/'data/qianchuan/daily_brief.db';db.parent.mkdir(parents=True)
   with sqlite3.connect(db) as c:
    c.execute('CREATE TABLE jobs(account,day,payload,sent)');c.execute('INSERT INTO jobs VALUES(?,?,?,1)',('123','2026-01-01',json.dumps(bundle)))
   store=v.Store(root);self.assertTrue(v.ingest_sent(root,'123',NOW,store));self.assertEqual(store.snapshot('123')['cases'],[])
   receipt={'complete':True,'parts':{str(i):{'verified':True,'message_id':'synthetic'+str(i)} for i in range(len(bundle['files'])+1)}}
   (folder/'delivery.json').write_text(json.dumps(receipt));before=(folder/'result.json').read_bytes()
   self.assertEqual(v.ingest_sent(root,'123',NOW,store),[]);self.assertTrue(store.snapshot('123')['cases']);self.assertEqual((folder/'result.json').read_bytes(),before)
   bundle['input_hashes']={'result.json':'bad-hash'}
   with sqlite3.connect(db) as c:c.execute('UPDATE jobs SET payload=?',(json.dumps(bundle),))
   self.assertTrue(v.ingest_sent(root,'123',NOW,store))
if __name__=='__main__':unittest.main()
