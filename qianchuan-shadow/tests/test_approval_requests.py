import unittest,tempfile,json
from unittest.mock import Mock
from qc_shadow.approval_requests import ApprovalRequests
class RequestTests(unittest.TestCase):
 def test_uncertain_send_not_replayed(self):
  with tempfile.TemporaryDirectory() as d:
   reader=Mock(actor='ou_actor',chat='oc_chat');reader.api.BASE='https://open.feishu.cn/open-apis';reader.api._tenant_token.return_value='fake';reader.api._request.side_effect=TimeoutError()
   q=ApprovalRequests(d,reader);a=q.request('a','p','300','400','cycle','2026-11-04T10:00:00+08:00');b=q.request('a','p','300','400','cycle','2026-11-04T10:01:00+08:00')
   self.assertEqual(a,b);self.assertEqual(reader.api._request.call_count,1);self.assertEqual(q.poll('2026-11-04T10:02:00+08:00'),[])
 def test_success_request_binding_and_expiry(self):
  with tempfile.TemporaryDirectory() as d:
   reader=Mock(actor='ou_actor',chat='oc_chat');reader.api.BASE='https://open.feishu.cn/open-apis';reader.api._tenant_token.return_value='fake';reader.api.BASE='https://open.feishu.cn/open-apis';reader.api._request.return_value={'code':0,'data':{'message_id':'om_request'}}
   reader.matching_reply_ids.return_value=[]
   q=ApprovalRequests(d,reader);key=q.request('a','p','300','400','cycle','2026-11-04T10:00:00+08:00')
   self.assertEqual(q.poll('2026-11-05T10:00:00+08:00'),[]);self.assertTrue((q.home/key/'invalidated.json').exists())
 def test_account_invalidation_includes_other_plan(self):
  with tempfile.TemporaryDirectory() as d:
   reader=Mock(actor='ou_actor',chat='oc_chat');reader.api.BASE='https://open.feishu.cn/open-apis';reader.api._tenant_token.return_value='fake';reader.api._request.return_value={'code':0,'data':{'message_id':'om_request'}}
   q=ApprovalRequests(d,reader);keys=[q.request('a',p,'300','400','cycle','2026-11-04T10:00:00+08:00') for p in ('p','p2')]
   q.invalidate_account('a','low_balance','2026-11-04T10:01:00+08:00')
   self.assertTrue(all((q.home/k/'invalidated.json').exists() for k in keys))
 def test_intermediate_low_balance_invalidates_after_recovery(self):
  import sqlite3
  from pathlib import Path
  with tempfile.TemporaryDirectory() as d:
   reader=Mock(actor='ou_actor',chat='oc_chat');reader.api.BASE='https://open.feishu.cn/open-apis';reader.api._tenant_token.return_value='fake';reader.api._request.return_value={'code':0,'data':{'message_id':'om_request'}}
   q=ApprovalRequests(Path(d)/'approvals',reader);key=q.request('a','p','300','400','cycle','2026-11-04T10:00:00+08:00')
   dbpath=Path(d)/'data/qianchuan/monitor.db';dbpath.parent.mkdir(parents=True)
   with sqlite3.connect(dbpath) as db:
    db.execute('CREATE TABLE balances(account TEXT,stamp TEXT,payload TEXT)')
    for time,balance in [('10:05:00','1000'),('10:10:00','400'),('10:15:00','1000')]:
     at='2026-11-04T'+time+'+08:00';db.execute('INSERT INTO balances VALUES(?,?,?)',('a',at,json.dumps({'advertiser_id':'a','fetched_at':at,'amounts_yuan':{'account_valid':balance}})))
   self.assertTrue(q.invalidate_observed_balances(d));self.assertTrue((q.home/key/'invalidated.json').exists())
