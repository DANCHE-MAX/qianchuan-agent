import unittest
from qc_shadow.refund_authority import apply_authority,model_hash
class RefundTests(unittest.TestCase):
 def test_explicit_history_mapping_and_config_drift(self):
  model={'season':'autumn_winter','version':'1.0','refund_rates':{'autumn_winter':{'gt300':0.69}}}
  p={'plan_id':'p','refund':{}};s={'account_id':'a','plans':[p]}
  auth={'account_id':'a','basis':'user_confirmed_model_assumption','historical_use':True,'model_sha256':model_hash(model),'plans':{'p':'gt300'},'evidence':'chat:confirmed'}
  apply_authority(s,auth,model);self.assertTrue(p['refund']['verified']);self.assertEqual(p['refund']['rate'],'0.69')
  model['version']='2';p['refund']={};apply_authority(s,auth,model);self.assertFalse(p['refund'].get('verified',False))
 def test_wrong_account_or_no_history_not_promoted(self):
  s={'account_id':'a','plans':[{'plan_id':'p','refund':{}}]}
  apply_authority(s,{'account_id':'b'},{});self.assertEqual(s['plans'][0]['refund'],{})
