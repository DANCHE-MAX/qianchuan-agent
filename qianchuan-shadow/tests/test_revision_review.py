import unittest,tempfile,hashlib,json
from pathlib import Path
from qc_shadow.revision_review import source_proof,add_note
class ReviewTests(unittest.TestCase):
 def test_original_seed_evidence_is_found_by_hash(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);daily=root/'evidence/daily';daily.mkdir(parents=True);seed=root/'history-20261104';seed.mkdir();raw=b'{"original":true}';(seed/'daily-test-plan.json').write_bytes(raw)
   p=source_proof(daily,'daily-test-plan.json:sha256:'+hashlib.sha256(raw).hexdigest());self.assertTrue(p['verified'])
 def test_note_is_not_approval_and_preserves_queue(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);q={'revisions':[{'revision_id':'r','review_status':'待人审'}]};(root/'queue.json').write_text(json.dumps(q));path=add_note(root,'r','核对说明')
   n=json.loads(Path(path).read_text());self.assertFalse(n['execution_clearance']);self.assertEqual(json.loads((root/'queue.json').read_text()),q)
