import importlib.util,unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import Mock
spec=importlib.util.spec_from_file_location('trial',Path(__file__).with_name('run.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
T=datetime.fromisoformat('2026-11-02T14:00:00+08:00')
def sample(i,total=None):return {'advertiser_id':'999','fetched_at':(T+timedelta(minutes=15*i)).isoformat(),'amounts_yuan':{'account_total':str(1000-10*i if total is None else total),'account_valid':str(1000-10*i if total is None else total),'account_frozen':'0'}}
def state():return {'id':'test','started_at':T.isoformat(),'account':'999','status':'active','points':[dict(m.item({'id':'test'},0,T),sample=sample(0))]}
class Tests(unittest.TestCase):
 def test_not_early(self):
  s=state();f=Mock();m.advance(s,f,T+timedelta(minutes=14));f.assert_not_called();self.assertEqual(len(s['points']),1)
 def test_four_intervals(self):
  s=state()
  for i in range(1,5):m.advance(s,lambda i=i:sample(i),T+timedelta(minutes=15*i))
  self.assertEqual(s['status'],'completed');self.assertEqual(m.Decimal(m.result(s)['rate']),m.Decimal('40'))
 def test_repeat_no_collection(self):
  s=state();m.advance(s,lambda:sample(1),T+timedelta(minutes=15));f=Mock();m.advance(s,f,T+timedelta(minutes=15));f.assert_not_called()
 def test_missing_never_backfilled(self):
  s=state();f=Mock(return_value=sample(4));m.advance(s,f,T+timedelta(minutes=60));self.assertEqual(f.call_count,1);self.assertIsNone(m.result(s)['rate'])
 def test_recharge_not_rate(self):
  s=state()
  for i in range(1,5):m.advance(s,lambda i=i:sample(i,1200 if i==2 else None),T+timedelta(minutes=15*i))
  self.assertIsNone(m.result(s)['rate'])
 def test_wrong_account(self):
  s=state();v=sample(1);v['advertiser_id']='888';m.advance(s,lambda:v,T+timedelta(minutes=15));self.assertIsNone(s['points'][1]['sample'])
 def test_send_once_and_uncertain_not_replayed(self):
  with tempfile.TemporaryDirectory() as d:
   s=state();p=s['points'][0];f=Mock();f.send_card.return_value={'data':{'message_id':'mock'}};path=Path(d)/'s.json'
   m.send_item(s,p,{},f,path,T);m.send_item(s,p,{},f,path,T);self.assertEqual(f.send_card.call_count,1)
   p['delivery']='sending';m.send_item(s,p,{},f,path,T);self.assertEqual(p['delivery'],'uncertain');self.assertEqual(f.send_card.call_count,1)
 def test_exception_uncertain(self):
  with tempfile.TemporaryDirectory() as d:
   s=state();p=s['points'][0];f=Mock();f.send_card.side_effect=TimeoutError();m.send_item(s,p,{},f,Path(d)/'s.json',T);self.assertEqual(p['delivery'],'uncertain')
if __name__=='__main__':unittest.main()
