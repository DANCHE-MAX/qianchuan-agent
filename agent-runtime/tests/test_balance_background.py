import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import Mock
from agent import balance_background as m
T=datetime.fromisoformat('2026-11-02T16:00:00+08:00')
def sample(i):
 return {'advertiser_id':'999','fetched_at':(T+timedelta(minutes=5*i)).isoformat(),'amounts_yuan':{'account_total':str(1000-10*i),'account_valid':str(1000-10*i),'account_frozen':'0'}}
def state():return {'id':'test5','started_at':T.isoformat(),'account':'999','status':'active','points':[dict(m.item({'id':'test5'},0,T),sample=sample(0))]}
class Tests(unittest.TestCase):
 def test_five_minute_nodes_and_twenty_minute_completion(self):
  s=state()
  for i in range(1,5):m.advance(s,lambda i=i:sample(i),T+timedelta(minutes=5*i))
  self.assertEqual(s['status'],'completed');self.assertEqual(len(s['points']),5);self.assertAlmostEqual(float(m.result(s)['rate']),120)
 def test_no_early_or_duplicate_fetch(self):
  s=state();f=Mock(return_value=sample(1));m.advance(s,f,T+timedelta(minutes=4));self.assertEqual(len(s['points']),1)
  m.advance(s,f,T+timedelta(minutes=5));m.advance(s,f,T+timedelta(minutes=5));self.assertEqual(f.call_count,1)
 def test_missed_slots_not_backfilled(self):
  s=state();f=Mock(return_value=sample(4));m.advance(s,f,T+timedelta(minutes=20));self.assertEqual(f.call_count,1);self.assertIsNone(m.result(s)['rate'])
 def test_uncertain_never_replayed(self):
  s=state();p=s['points'][0];p['delivery']='sending';f=Mock()
  with tempfile.TemporaryDirectory() as d:m.send_item(s,p,{},f,Path(d)/'s.json',T)
  self.assertEqual(p['delivery'],'uncertain');f.send_card.assert_not_called()
