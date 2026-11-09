import unittest,tempfile,json,hashlib
from pathlib import Path
from agent import historical_evidence as h
class EvidenceTests(unittest.TestCase):
 def test_bound_evidence_and_tampering(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder=root/'out/demo';folder.mkdir(parents=True)
   for name in ('result.json','delivery.json','profit.png','profit.xlsx'):(folder/name).write_text(name)
   ledger={'old':'card'};files={str(p.relative_to(root)):h.digest(p) for p in folder.iterdir()}
   proof=root/'data/report_evidence/demo/2026-01-01.json';proof.parent.mkdir(parents=True)
   data=dict(version=1,account='demo',date='2026-01-01',directory='out/demo',ledger_sha256=h.ledger_digest(ledger),files=files,artifacts=[{'path':'out/demo/profit.png','kind':'image'},{'path':'out/demo/profit.xlsx','kind':'file'}],verification='remote_attachments_and_excel_verified')
   proof.write_text(json.dumps(data));self.assertEqual(h.load(root,'demo','2026-01-01',ledger)['directory'],folder.resolve())
   with self.assertRaises(ValueError):h.load(root,'demo','2026-01-01',{'different':'ledger'})
   (folder/'profit.xlsx').write_text('changed')
   with self.assertRaises(ValueError):h.load(root,'demo','2026-01-01',ledger)
 def test_absence_does_not_reconstruct_history(self):
  with tempfile.TemporaryDirectory() as t:self.assertIsNone(h.load(Path(t),'demo','2026-01-01',{}))
 def test_frozen_hash_mismatch_never_uses_retrospective_proof(self):
  import sqlite3
  from unittest.mock import patch
  from agent import decision_dashboard as d
  with tempfile.TemporaryDirectory() as t:
   root=Path(t).resolve();(root/'data/qianchuan').mkdir(parents=True);folder=root/'out/demo';folder.mkdir(parents=True);(folder/'result.json').write_text('{}')
   with sqlite3.connect(root/'data/qianchuan/daily_brief.db') as db:
    db.execute('CREATE TABLE jobs(account TEXT,day TEXT,sent INTEGER,payload TEXT)')
    db.execute('INSERT INTO jobs VALUES(?,?,1,?)',('demo','2026-01-01',json.dumps({'directory':str(folder),'input_hashes':{'result.json':'wrong'}})))
   with patch.object(h,'load') as mock:
    with self.assertRaises(ValueError):d.completed(root,'demo','2026-01-01')
    mock.assert_not_called()
