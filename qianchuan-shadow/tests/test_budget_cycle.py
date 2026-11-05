import unittest,tempfile,json
from pathlib import Path
from unittest.mock import Mock,patch
from datetime import datetime
from qc_shadow.budget_cycle import run_cycle
from qc_shadow.execution_store import ExecutionStore
from qc_shadow.control_evidence import record_snapshot
import test_preflight as fixtures
class Fixed(datetime):
 @classmethod
 def now(cls,tz=None):return datetime.fromisoformat('2026-11-04T10:00:11+08:00')
class CycleTests(unittest.TestCase):
 def setup_data(self,d):
  s,l,a=fixtures.PreflightTests().fixture();h=Path(d);(h/'activation.json').write_text(json.dumps(a));ExecutionStore(h/'execution.db').freeze('a','p','300','2026-11-04','proof');record_snapshot(h,s);return h,s,l
 def test_missing_history_never_calls_network_or_write(self):
  with tempfile.TemporaryDirectory() as d:
   h,s,l=self.setup_data(d);s['plans'][0]['daily'].pop();record_snapshot(h,s);reader=Mock()
   with patch('qc_shadow.budget_cycle.datetime',Fixed):r=run_cycle(h,'source',[s],reader_factory=reader)
   reader.assert_not_called();self.assertEqual(r['actions'],[])
 def test_full_chain_writes_once_after_fresh_second_check(self):
  with tempfile.TemporaryDirectory() as d:
   h,s,l=self.setup_data(d);reader=Mock();reader.capture.return_value=l;reader.readback.return_value='400';factory=Mock(return_value=reader);identity=Mock(actor='ou_actor',chat='oc_chat')
   write=Mock(return_value={'code':0,'data':{'results':[{'ad_id':'p','status':'SUCCESS'}]}})
   with patch('qc_shadow.budget_cycle.datetime',Fixed),patch('qc_shadow.budget_cycle.clock',return_value='2026-11-04T10:00:11+08:00'),patch('qc_shadow.budget_cycle.OfficialBudgetWriter',return_value=write):
    r=run_cycle(h,'source',[s],factory,lambda _:identity)
   self.assertEqual(r['actions'][0]['status'],'success');self.assertEqual(reader.capture.call_count,2);self.assertEqual(write.call_count,1)
 def test_second_check_low_balance_blocks_write(self):
  import copy
  with tempfile.TemporaryDirectory() as d:
   h,s,l=self.setup_data(d);bad=copy.deepcopy(l);bad['available']='500';reader=Mock();reader.capture.side_effect=[l,bad];identity=Mock(actor='ou_actor',chat='oc_chat');write=Mock()
   with patch('qc_shadow.budget_cycle.datetime',Fixed),patch('qc_shadow.budget_cycle.clock',return_value='2026-11-04T10:00:11+08:00'),patch('qc_shadow.budget_cycle.OfficialBudgetWriter',return_value=write):
    r=run_cycle(h,'source',[s],lambda *_:reader,lambda _:identity)
   write.assert_not_called();self.assertEqual(r['actions'][0]['status'],'blocked')
 def test_kill_switch_changed_during_reads_prevents_write(self):
  with tempfile.TemporaryDirectory() as d:
   h,s,l=self.setup_data(d);reader=Mock();identity=Mock(actor='ou_actor',chat='oc_chat');write=Mock()
   def capture(*_):
    a=json.loads((h/'activation.json').read_text());a['authorized']=False;(h/'activation.json').write_text(json.dumps(a));return l
   reader.capture.side_effect=capture
   with patch('qc_shadow.budget_cycle.datetime',Fixed),patch('qc_shadow.budget_cycle.clock',return_value='2026-11-04T10:00:11+08:00'),patch('qc_shadow.budget_cycle.OfficialBudgetWriter',return_value=write):run_cycle(h,'source',[s],lambda *_:reader,lambda _:identity)
   write.assert_not_called()
 def test_account_low_balance_invalidates_static_skipped_plan(self):
  import copy
  from qc_shadow.approval_requests import ApprovalRequests
  with tempfile.TemporaryDirectory() as d:
   h,s,l=self.setup_data(d);second=copy.deepcopy(s['plans'][0]);second['plan_id']='p2';second['daily'].pop();s['plans'].append(second)
   act=json.loads((h/'activation.json').read_text());act['plans'].append('p2');(h/'activation.json').write_text(json.dumps(act));record_snapshot(h,s)
   identity=Mock(actor='ou_actor',chat='oc_chat');identity.api.BASE='https://open.feishu.cn/open-apis';identity.api._tenant_token.return_value='fake';identity.api._request.return_value={'code':0,'data':{'message_id':'om_request'}}
   requests=ApprovalRequests(h/'approvals',identity);key=requests.request('a','p2','300','400','cycle','2026-11-04T10:00:00+08:00')
   l['available']='500';reader=Mock();reader.capture.return_value=l
   with patch('qc_shadow.budget_cycle.datetime',Fixed),patch('qc_shadow.budget_cycle.clock',return_value='2026-11-04T10:00:11+08:00'):run_cycle(h,'source',[s],lambda *_:reader,lambda _:identity)
   self.assertTrue((h/'approvals'/key/'invalidated.json').exists())
