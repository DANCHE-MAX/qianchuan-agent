import json,tempfile,unittest
from pathlib import Path
from datetime import date,timedelta
from qc_shadow.__main__ import output_path,DEFAULT_SOURCE
from qc_shadow.adapter import Source
from test_engine import fixture,run
import test_engine
import test_data
class ReviewTests(unittest.TestCase):
 good=test_engine.EngineTests.good
 def test_output_always_protects_production(self):
  with self.assertRaises(ValueError):output_path(Path(DEFAULT_SOURCE)/'forbidden.db','/private/tmp/test-source')
 def test_break_even_is_recorded_not_late(self):
  d=fixture(spend='55.66',gmv='100',orders=1);r=run(d)
  self.assertIn({'event':'node_evaluated','node':3,'window_end':'2026-11-01'},r['state_proposals'])
  d['state']['evaluated_nodes']=[3];d['as_of']='2026-11-03T10:00:00+08:00';d['daily'].append(dict(d['daily'][-1],day='2026-11-02'))
  self.assertNotIn('late_node_policy_unresolved',run(d)['blockers'])
 def test_protection_requires_grant_time(self):
  d=self.good();d['state'].update(stage='to30',protected_until='2026-11-20',protection_evidence='e')
  self.assertIn('protection_grant_unverified',run(d)['blockers'])
 def test_intraday_separate_clock(self):
  d=self.good();d.update(as_of='2026-11-02T09:00:00+08:00',evaluation_kind='intraday',daily_assessment_evidence='prior:assessment',daily_assessed_through='2026-11-01')
  self.assertEqual(run(d)['action'],'increase')
 def test_cumulative_loss_not_invented_as_extra_increase_ban(self):
  d=self.good();d['daily'][0].update(spend='1000',gmv='1');d['daily'][-1]['orders']=10
  self.assertEqual(run(d)['action'],'increase')
 def test_expired_request_is_invalidated(self):
  from test_engine import BoundaryTests
  d=BoundaryTests().approval_input();d['approval']['expires_at']='2026-11-02T10:00:00+08:00'
  self.assertEqual(run(d)['invalidated_requests'],['r1'])
class BudgetOrderingTests(unittest.TestCase):
 setUp=test_data.DataTests.setUp
 def test_missing_row_timestamp_still_selects_newest(self):
  folder=self.root/'data/qianchuan'
  for name,stamp,amount in [('a','2026-11-01T08:00:00+08:00','100'),('b','2026-11-01T09:00:00+08:00','200')]:
   (folder/('budgets_'+name+'.json')).write_text(json.dumps({'advertiser_id':'9','kind':'current_budget','fetched_at':stamp,'plans':[{'plan_id':'1','amount':amount,'mode':'BUDGET_MODE_DAY','status':'ok'}]}))
  from unittest.mock import patch
  with patch.object(Path,'glob',return_value=[folder/'budgets_a.json',folder/'budgets_b.json']):
   self.assertEqual(Source(self.root).snapshot('9','2026-11-02T10:00:00+08:00')['plans'][0]['budget']['amount'],'200')

class IncompleteNodeTests(unittest.TestCase):
 def test_deferred_node_does_not_propose_completed_event(self):
  d=fixture(spend='100',gmv='1',orders=1);d['refund_rate']=None
  self.assertEqual(run(d)['state_proposals'],[])
