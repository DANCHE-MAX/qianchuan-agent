import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
import test_roi_shadow
from qc_shadow.roi_cycle import run_cycle
class CycleTests(unittest.TestCase):
 def run_sample(self,pending=False,failed=False):
  p,s=test_roi_shadow.RoiTests().fixture()
  if failed:s['status']='failed';s['code']=40100
  snapshot={'account_id':'1','plans':[p],'pending_execution_revisions':[{'plan_id':'p','day':'2026-11-03','before':{},'after':{}}] if pending else []}
  with tempfile.TemporaryDirectory() as t,patch('qc_shadow.roi_cycle.recovery_ensure',return_value={'status':'ok','plans':{}}),patch('qc_shadow.roi_cycle.commerce_ensure',return_value={'status':'ok','rows':[],'reconciliation':{}}),patch('qc_shadow.roi_cycle.acquire',return_value={'settings':[s]}),patch('qc_shadow.roi_cycle.clock',return_value='2026-11-04T10:36:00+08:00'):
   a=run_cycle(t,'unused',[snapshot]);first=json.loads((Path(t)/'latest.json').read_text());b=run_cycle(t,'unused',[snapshot]);second=json.loads((Path(t)/'latest.json').read_text())
  self.assertEqual(first['plans'][0]['case_id'],second['plans'][0]['case_id']);return a,first
 def test_revision_blocks_numeric_suggestion(self):
  a,r=self.run_sample(pending=True);self.assertIsNone(r['plans'][0]['candidate_roi']);self.assertFalse(r['roi_write_enabled'])
 def test_api_failure_is_reported(self):
  a,r=self.run_sample(failed=True);self.assertEqual(a['error_count'],1);self.assertEqual(r['errors'][0]['code'],40100)
 def test_repeat_facts_are_same_case(self):self.run_sample()
 def test_queue_retains_revisions_from_earlier_account(self):
  p,s=test_roi_shadow.RoiTests().fixture();revisions=[{'plan_id':'p','day':'2026-11-03','before':{},'after':{}}]
  snapshots=[{'account_id':'1','plans':[p],'pending_execution_revisions':revisions},{'account_id':'2','plans':[p]}]
  with tempfile.TemporaryDirectory() as t,patch('qc_shadow.roi_cycle.recovery_ensure',return_value={'status':'ok','plans':{}}),patch('qc_shadow.roi_cycle.commerce_ensure',return_value={'status':'ok','rows':[],'reconciliation':{}}),patch('qc_shadow.roi_cycle.acquire',return_value={'settings':[s]}),patch('qc_shadow.roi_cycle.clock',return_value='2026-11-04T10:36:00+08:00'):
   run_cycle(t,'unused',snapshots);self.assertEqual(len(json.loads((Path(t)/'review/queue.json').read_text())['revisions']),1)
