import json,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.hourly import CHINA
from tests.test_balance_cycle import sample
NOW=datetime(2026,1,2,10,tzinfo=CHINA)
def samples(end=NOW):
 # Continuous observations; current=100/h, preceding=50/h, yesterday=25/h.
 rows=[]
 for i in range(9):
  at=end-timedelta(minutes=30*(8-i));v=1200-25*min(i,4)-50*max(0,i-4)
  rows.append(sample(at,str(v)))
 for i in range(5):rows.append(sample(end-timedelta(days=1,hours=2)+timedelta(minutes=i*30),str(2000-i*12.5)))
 return sorted(rows,key=lambda x:x['fetched_at'])

class ComparisonTests(unittest.TestCase):
 def build(self,rows=None,now=NOW,current=True):
  from agent.balance_comparison import build
  rows=samples(now) if rows is None else rows
  return build(rows,'999',now,rows[-1] if current else None)
 def test_three_exact_windows_and_percentages(self):
  v=self.build();self.assertEqual([r['rate'] for r in v['rows']],['100','50','25'])
  self.assertEqual(v['previous_change'],'100');self.assertEqual(v['yesterday_change'],'300')
  self.assertEqual(v['rows'][1]['window'],'01-02 06:00—08:00')
 def test_midnight_previous_and_yesterday_are_different_dates(self):
  v=self.build(now=NOW.replace(hour=0))
  self.assertEqual(v['rows'][0]['window'],'01-01 22:00—00:00')
  self.assertEqual(v['rows'][2]['window'],'12-31 22:00—00:00')
 def test_missing_yesterday_is_unknown_not_zero(self):
  rows=[r for r in samples() if r['fetched_at'][:10]=='2026-01-02']
  v=self.build(rows);self.assertIsNone(v['rows'][2]['rate']);self.assertIsNone(v['yesterday_change'])
 def test_failed_current_read_has_no_cached_current_balance(self):
  v=self.build(current=False);self.assertIsNone(v['available']);self.assertIsNone(v['rows'][0]['rate'])
 def test_recharge_blocks_only_affected_window(self):
  rows=samples();rows[-2]['amounts_yuan']['account_total']='9999'
  v=self.build(rows);self.assertIsNone(v['rows'][0]['rate']);self.assertEqual(v['rows'][1]['rate'],'50')
 def test_other_account_does_not_supply_history(self):
  rows=samples()
  for r in rows:
   if r['fetched_at'][:10]=='2026-01-01':r['advertiser_id']='888'
  self.assertIsNone(self.build(rows)['rows'][2]['rate'])
 def test_zero_reference_does_not_compute_infinite_percentage(self):
  from agent.balance_comparison import percent
  self.assertIsNone(percent('10','0'));self.assertEqual(percent('0','10'),'-100')
 def test_existing_schedule_enqueues_image_and_deduplicates(self):
  from tests.test_balance_cycle import Tests as Fixture,NOW as FIXED
  from agent import balance_comparison as b
  fixture=Fixture();fixture.setUp()
  try:
   (fixture.root/'config/profit_daily.json').write_text('{}')
   with patch.object(b,'deliver',return_value={}) as send:
    fixture.tick(FIXED);fixture.tick(FIXED+timedelta(minutes=1))
   self.assertEqual(send.call_count,1)
   payload=send.call_args.args[3]
   self.assertIn('_balance_comparison',payload)
   self.assertIsNone(payload['_balance_comparison']['rows'][2]['rate'])
   fixture.nf.send_card.assert_not_called()
  finally:fixture.doCleanups()
 def test_missing_middle_sample_does_not_become_zero(self):
  rows=samples();rows.pop(-2)
  v=self.build(rows);self.assertIsNone(v['rows'][0]['rate'])
 def test_previous_wrong_time_does_not_substitute_latest(self):
  rows=samples();rows=[r for r in rows if not r['fetched_at'].startswith('2026-01-02T08:00')]
  self.assertIsNone(self.build(rows)['rows'][1]['rate'])
 def test_unknown_send_never_repeats(self):
  from agent import balance_comparison as b
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);n=Mock();n.chat_id='chat';n.send_card.side_effect=TimeoutError()
   data={'_balance_comparison':self.build(),'elements':[],'header':{}}
   with patch.object(b,'render',side_effect=lambda root,view,path:path.write_bytes(b'png')),patch.object(b,'upload',return_value='img_fake'):
    with self.assertRaises(TimeoutError):b.deliver(root,n,'key',data,'uuid')
    with self.assertRaises(RuntimeError):b.deliver(root,n,'key',data,'uuid')
   self.assertEqual(n.send_card.call_count,1)
 def test_known_message_readback_retry_preserves_mentions(self):
  from agent import balance_comparison as b
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);n=Mock();n.chat_id='chat';n.send_card.return_value={'data':{'message_id':'msg'}}
   content={'elements':[{'tag':'img','img_key':'img_fake'}]}
   n.get_message.side_effect=[TimeoutError(),[{'chat_id':'chat','msg_type':'interactive','body':{'content':json.dumps(content)}}]]
   mention={'tag':'div','text':{'tag':'lark_md','content':'<at id="ou_synthetic"></at>'}}
   data={'_balance_comparison':self.build(),'elements':[{'tag':'div','text':{'content':'old text'}},mention],'header':{}}
   with patch.object(b,'render',side_effect=lambda root,view,path:path.write_bytes(b'png')),patch.object(b,'upload',return_value='img_fake'):
    with self.assertRaises(TimeoutError):b.deliver(root,n,'key',data,'uuid')
    b.deliver(root,n,'key',data,'uuid')
   self.assertEqual(n.send_card.call_count,1);sent=n.send_card.call_args.args[0]
   self.assertIn(mention,sent['elements']);self.assertNotIn('_balance_comparison',sent)

if __name__=='__main__':unittest.main()
