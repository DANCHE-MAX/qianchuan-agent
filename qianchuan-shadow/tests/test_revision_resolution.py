import unittest,tempfile,json,hashlib,copy
from pathlib import Path
from qc_shadow.plan_audit import revision_id
from qc_shadow.revision_resolution import apply_resolutions
class ResolutionTests(unittest.TestCase):
 def fixture(self,root):
  daily=root/'daily';batch=daily/'batch-test';batch.mkdir(parents=True);home=root/'resolutions';home.mkdir()
  def row(gmv,orders,side):
   raw=side.encode();name='daily-'+side+'-plan.json';(batch/name).write_bytes(raw)
   return dict(plan_id='p',day='2026-11-03',spend='144.77',gmv=gmv,orders=orders,complete=True,daily_aggregate_matches=True,evidence=name+':sha256:'+hashlib.sha256(raw).hexdigest())
  before=row('900.5',5,'before');after=row('861.5',4,'after');r=dict(plan_id='p',day='2026-11-03',before=before,after=after)
  s={'account_id':'a','plans':[{'plan_id':'p','daily':[after]}],'pending_execution_revisions':[r],'gaps':['data_revision_pending_execution_review','other_gap']}
  record={'revision_id':revision_id(r),'account_id':'a','decision':'accept_revised_facts','authorization_source':'explicit_user_chat','reason':'用户确认退款','authorized_at':'2026-11-04T15:00:00+08:00','user_instruction':'处理这条修订审核'}
  path=home/(revision_id(r)+'.json');path.write_text(json.dumps(record));return s,r,home,daily,path
 def test_exact_revision_clears_only_review_gate(self):
  with tempfile.TemporaryDirectory() as t:
   s,r,h,d,p=self.fixture(Path(t));apply_resolutions(s,h,d)
   self.assertEqual(s['pending_execution_revisions'],[]);self.assertEqual(s['gaps'],['other_gap']);self.assertEqual(len(s['reviewed_execution_revisions']),1);self.assertTrue(p.exists())
 def test_wrong_account_does_not_clear(self):
  with tempfile.TemporaryDirectory() as t:
   s,r,h,d,p=self.fixture(Path(t));s['account_id']='b';apply_resolutions(s,h,d);self.assertEqual(s['pending_execution_revisions'],[r])
 def test_subsequent_fact_change_invalidates_resolution(self):
  with tempfile.TemporaryDirectory() as t:
   s,r,h,d,p=self.fixture(Path(t));s['plans'][0]['daily']=[dict(r['after'],gmv='800')];apply_resolutions(s,h,d);self.assertEqual(s['pending_execution_revisions'],[r])
 def test_new_revision_remains_pending(self):
  with tempfile.TemporaryDirectory() as t:
   s,r,h,d,p=self.fixture(Path(t));other=dict(r,day='2026-11-02');s['pending_execution_revisions'].append(other);apply_resolutions(s,h,d)
   self.assertEqual(s['pending_execution_revisions'],[other]);self.assertIn('data_revision_pending_execution_review',s['gaps'])
 def test_original_proof_tamper_blocks(self):
  with tempfile.TemporaryDirectory() as t:
   s,r,h,d,p=self.fixture(Path(t));(d/'batch-test/daily-before-plan.json').write_text('changed');apply_resolutions(s,h,d);self.assertEqual(s['pending_execution_revisions'],[r])
