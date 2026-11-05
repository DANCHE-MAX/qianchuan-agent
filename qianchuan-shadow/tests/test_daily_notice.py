import unittest,tempfile
from unittest.mock import Mock
from qc_shadow.daily_notice import deliver
class Notices(unittest.TestCase):
 def test_normal_silent(self):
  with tempfile.TemporaryDirectory() as d:
   send=Mock();self.assertEqual(deliver(d,{'status':'ok'},send),'silent');send.assert_not_called()
 def test_sent_only_once(self):
  with tempfile.TemporaryDirectory() as d:
   send=Mock();s={'status':'failed','target_day':'2026-11-03'}
   self.assertEqual(deliver(d,s,send),'sent');self.assertEqual(deliver(d,s,send),'sent');self.assertEqual(send.call_count,1)
 def test_uncertain_never_replayed(self):
  with tempfile.TemporaryDirectory() as d:
   send=Mock(side_effect=TimeoutError());s={'status':'ok','target_day':'2026-11-03','revisions':1}
   with self.assertRaises(TimeoutError):deliver(d,s,send)
   self.assertEqual(deliver(d,s,send),'uncertain');self.assertEqual(send.call_count,1)

 def test_verified_preflight_failure_can_send_once(self):
  import json
  from pathlib import Path
  with tempfile.TemporaryDirectory() as d:
   s={'status':'failed','target_day':'2026-11-03'}
   try:deliver(d,s,Mock(side_effect=TypeError()))
   except TypeError:pass
   p=next((Path(d)/'daily_notices').glob('*.json'));r=json.loads(p.read_text());r['delivery']='not_sent_preflight';p.write_text(json.dumps(r))
   send=Mock();self.assertEqual(deliver(d,s,send),'sent');self.assertEqual(send.call_count,1)
