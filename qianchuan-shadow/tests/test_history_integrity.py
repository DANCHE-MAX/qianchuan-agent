import json,unittest,hashlib
from qc_shadow.history_source import enrich
class IntegrityTests(unittest.TestCase):
 def test_empty_manifest_cannot_claim_verified(self):
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'bad.json';p.write_text(json.dumps({'version':'source-history-v2','account_id':'9','built_at':'2026-11-02T09:00:00+08:00','evidence_files':[],'first_enabled':{'1':{'verified':True,'first_enabled_at':'2026-10-01T00:00:00+08:00','evidence':'nonexistent'}},'daily':[]}))
   result={'account_id':'9','as_of':'2026-11-02T10:00:00+08:00','plans':[],'source_fingerprint':{},'gaps':[]}
   with self.assertRaises(ValueError):enrich(result,p)

import test_history_integration
class DerivedIntegrityTests(unittest.TestCase):
 setUp=test_history_integration.IntegrationTests.setUp
 bundle=test_history_integration.IntegrationTests.bundle
 def test_normalized_metric_tampering_rejected(self):
  from qc_shadow.adapter import Source
  p=self.bundle();s=json.loads(p.read_text());s['daily'][0]['gmv']='999999';p.write_text(json.dumps(s))
  with self.assertRaises(ValueError):Source(self.root,history_path=p).snapshot('9','2026-11-02T10:00:00+08:00')

class RefundProofTests(unittest.TestCase):
 setUp=test_history_integration.IntegrationTests.setUp
 bundle=test_history_integration.IntegrationTests.bundle
 def test_pending_refund_mapping_cannot_be_promoted_by_bundle(self):
  from qc_shadow.adapter import Source
  p=self.bundle();h=json.loads(p.read_text());cfg=self.root/'config/rules.json'
  h['verified_refund_mappings']={'1':{'source_config_sha256':hashlib.sha256(cfg.read_bytes()).hexdigest(),'rate':'.5','band':'x','model_version':'v1','season':'winter','evidence':'fabricated','basis':'用户指定：托管采用季节整体退款率，不拆商品结构'}}
  p.write_text(json.dumps(h));s=Source(self.root,history_path=p).snapshot('9','2026-11-02T10:00:00+08:00')
  self.assertFalse(s['plans'][0]['refund']['verified'])

class RevisionProofTests(unittest.TestCase):
 setUp=test_history_integration.IntegrationTests.setUp
 bundle=test_history_integration.IntegrationTests.bundle
 def test_overlap_dependency_cannot_be_omitted(self):
  from qc_shadow.adapter import Source
  p=self.bundle();h=json.loads(p.read_text());h['evidence_files']=[v for v in h['evidence_files'] if v['file']!='overlap-check.json'];p.write_text(json.dumps(h))
  with self.assertRaises(ValueError):Source(self.root,history_path=p).snapshot('9','2026-11-02T10:00:00+08:00')
 def test_changed_overlap_dependency_rejected(self):
  from qc_shadow.adapter import Source
  p=self.bundle();(self.root/'overlap-check.json').write_text('{}')
  with self.assertRaises(ValueError):Source(self.root,history_path=p).snapshot('9','2026-11-02T10:00:00+08:00')
