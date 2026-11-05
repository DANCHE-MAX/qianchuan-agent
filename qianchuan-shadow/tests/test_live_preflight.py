import unittest,tempfile
from datetime import datetime
from qc_shadow.live_preflight import LiveReader,PreflightClient
class Fake:
 account='123';BALANCE=PreflightClient.BALANCE
 def __init__(self):self.calls=[];self.balance_account='123'
 def get(self,path,p):
  self.calls.append((path,p))
  if path==PreflightClient.DETAIL:return {'data':{'ad_id':456,'delivery_setting':{'budget':5000,'budget_mode':'BUDGET_MODE_DAY'}}}
  if path==self.BALANCE:return {'data':{'advertiser_id':self.balance_account,'account_valid':60000000}}
  if path==PreflightClient.REPORT:return {'code':0,'data':{'page_info':{'page':1,'total_number':1,'total_page':1},'rows':[{'dimensions':{'ad_id':{'Value':'456'}},'metrics':{'stat_cost_for_roi2':{'Value':'4500'}}}]}}
  if p['page_size']!=20:raise ValueError('official_log_page_size')
  return {'code':0,'data':{'page_info':{'page':1,'total_number':0,'total_page':0},'logs':[]}}
class LiveTests(unittest.TestCase):
 def test_paced_official_reads_and_unit(self):
  with tempfile.TemporaryDirectory() as d:
   f=Fake();r=LiveReader('unused',d,f,lambda:datetime.fromisoformat('2026-11-04T10:00:00+08:00'));r.paced.sleep=lambda _:None
   x=r.capture('456');self.assertEqual(x['available'],'600');self.assertEqual(x['today_spend'],'4500');self.assertTrue(x['logs_complete']);self.assertEqual(len(f.calls),4)
 def test_wrong_balance_identity_fails(self):
  with tempfile.TemporaryDirectory() as d:
   f=Fake();f.balance_account='other';r=LiveReader('unused',d,f);r.paced.sleep=lambda _:None
   with self.assertRaises(ValueError):r.capture('456')
