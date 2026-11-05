import unittest
from types import SimpleNamespace
from qc_shadow.roi_source import capture,RoiReadClient
from qc_shadow.read_transport import ReadFailure
class Fake:
 def __init__(self,bad=False,fail=False):self.client=SimpleNamespace(account='1');self.bad=bad;self.fail=fail;self.calls=[]
 def get(self,path,params):
  self.calls.append((path,params))
  if self.fail:raise ReadFailure(code=40100,request_id='r')
  return {'data':{'ad_id':3 if self.bad else 2,'marketing_goal':'VIDEO_PROM_GOODS','delivery_setting':{'roi2_goal':6},'product_infos':[{'product_id':8}]}}
class Tests(unittest.TestCase):
 def test_success_exact_plan_detail_only(self):
  c=Fake();r=capture(c,'2');self.assertEqual(r['target_roi'],6);self.assertEqual(r['product_ids'],['8']);self.assertEqual(len(c.calls),1);self.assertEqual(c.calls[0][1]['ad_id'],2)
 def test_identity_fail_closed(self):
  c=Fake(bad=True);r=capture(c,'2');self.assertEqual(r['status'],'failed');self.assertEqual(len(c.calls),1)
 def test_detail_failure_no_fake_value(self):
  c=Fake(fail=True);r=capture(c,'2');self.assertEqual(r['code'],40100);self.assertNotIn('target_roi',r);self.assertEqual(len(c.calls),1)
 def test_only_detail_read_endpoint(self):self.assertEqual(RoiReadClient.ALLOWED,{RoiReadClient.DETAIL})
