import unittest,tempfile,json
from pathlib import Path
from agent import local_report_review as review
from agent.historical_evidence import digest,ledger_digest
class Tests(unittest.TestCase):
 def test_readonly_unsent_review_and_changed_confirmation_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();folder=root/'out/review';(folder/'two_day').mkdir(parents=True)
   values={'result.json':{'date':'2026-01-01','account':'demo'},'preflight.json':{'date':'2026-01-01','allowed':True,'errors':[]},'two_day/collected.json':[{'day':'2026-01-01'}]}
   for n,v in values.items():(folder/n).write_text(json.dumps(v))
   confirmation=root/'data/refund_confirmations/confirmed.json';confirmation.parent.mkdir(parents=True);confirmation.write_text('{}')
   files={str(p.relative_to(root)):digest(p) for p in [*[folder/n for n in values],confirmation]};ledger={'frozen':'original'}
   index=root/'data/dashboard_reviews/demo/2026-01-01.json';index.parent.mkdir(parents=True);index.write_text(json.dumps(dict(account='demo',date='2026-01-01',status='reviewed_unsent',ledger_sha256=ledger_digest(ledger),files=files,directory='out/review',note='业务确认退款口径差异')))
   r,m=review.load(root,'demo','2026-01-01',ledger);self.assertEqual(r['_review_status'],'已复核未发送');self.assertNotIn('_review_status',json.loads((folder/'result.json').read_text()))
   confirmation.write_text('{"changed":true}')
   with self.assertRaises(ValueError):review.load(root,'demo','2026-01-01',ledger)
