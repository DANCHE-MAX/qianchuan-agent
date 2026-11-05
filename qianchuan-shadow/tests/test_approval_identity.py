import unittest,json
from qc_shadow.approval_identity import verify_reply
class ApprovalTests(unittest.TestCase):
 def fixtures(self):
  req={'request_id':'req','account_id':'a','plan_id':'p','before':'300','after':'400','rule_version':'business-v1.0','issued_at':'2026-11-04T10:00:00+08:00','expires_at':'2026-11-05T10:00:00+08:00','expected_actor_id':'ou_actor','chat_id':'oc_chat','message_id':'om_request'}
  message={'message_id':'om_reply','parent_id':'om_request','chat_id':'oc_chat','msg_type':'text','deleted':False,'updated':False,'create_time':'1793757900000','sender':{'id':'ou_actor','id_type':'open_id','sender_type':'user'},'body':{'content':json.dumps({'text':'同意预算 req'})}}
  # Derive fixture millis from explicit date, never rely on hand conversion.
  from datetime import datetime
  message['create_time']=str(int(datetime.fromisoformat('2026-11-04T10:05:00+08:00').timestamp()*1000))
  return req,message
 def verify(self,r,m,member=True):return verify_reply(r,m,'2026-11-04T10:06:00+08:00',member)
 def test_bound_human_reply(self):
  r,m=self.fixtures();a=self.verify(r,m);self.assertTrue(a['identity_verified']);self.assertEqual(a['status'],'approved')
 def test_wrong_sender_bot_group_parent_deleted_edited(self):
  import copy
  r,m=self.fixtures()
  for key,value in [('parent_id','other'),('chat_id','other'),('deleted',True),('updated',True),('msg_type','post')]:
   bad=copy.deepcopy(m);bad[key]=value;self.assertIsNone(self.verify(r,bad))
  for key,value in [('id','ou_other'),('id_type','user_id'),('sender_type','app')]:
   bad=copy.deepcopy(m);bad['sender'][key]=value;self.assertIsNone(self.verify(r,bad))
 def test_plain_agreement_no_member_or_expired_rejected(self):
  r,m=self.fixtures();self.assertIsNone(self.verify(r,m,False));m['body']['content']=json.dumps({'text':'同意'});self.assertIsNone(self.verify(r,m))
  r,m=self.fixtures();self.assertIsNone(verify_reply(r,m,'2026-11-05T10:00:00+08:00',True))
 def test_reply_before_request_rejected(self):
  r,m=self.fixtures();m['create_time']='1';self.assertIsNone(self.verify(r,m))
