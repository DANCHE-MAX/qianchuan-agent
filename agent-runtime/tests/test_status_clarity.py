"""User-facing state must distinguish unavailable evidence from zero results."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import analysis_gate as gate, balance_cycle, health, monitor as m, settings
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_collection_rates import NOW, ENV, balance, snapshot


class Tests(unittest.TestCase):
    def test_ready_message_does_not_repeat_failed_condition_warning(self):
        result={'state':'ready','reasons':[]}
        text=gate.describe(result)
        self.assertIn('条件已满足',text)
        self.assertNotIn('未满足条件',text)
        self.assertIn('不代表没有异常',text)

    def test_blocked_card_does_not_report_unassessed_objects_as_zero(self):
        with tempfile.TemporaryDirectory() as root:
            store=m.Store(root)
            result=gate.assess([snapshot(NOW,'10')],'999','plan',NOW)
            gate.record(store,result,NOW)
            messages=store.pending('999')
            self.assertTrue(messages)
            text=str(json.loads(messages[0][1]))
            self.assertIn('未评估',text)
            self.assertNotIn('完整可比对象0个',text)

    def test_health_completed_message_includes_issue_count(self):
        from test_health import Tests as HealthFixtures
        fixture=HealthFixtures();fixture.setUp()
        try:
            fixture.healthy()
            with patch.object(health.h,'now_china',return_value=fixture.now):
                status=health.inspect(fixture.root,{'QC_ADVERTISER_IDS':'999'},fixture.now)
            status['issues']={'intervals':'既有运行期缺口8个'};status['deferred']={}
            text=str(health.rendered(status))
            self.assertIn('发现1项问题',text)
        finally: fixture.doCleanups()

    def test_unknown_balance_speed_does_not_claim_fixed_rate_estimation(self):
        text=str(balance_cycle.notice(balance(NOW),{'rate':None,'reason':'缺少四小时前基准'}))
        self.assertNotIn('按近四小时余额净下降速率保持不变估算',text)
        self.assertIn('未计算',text)
        self.assertIn('充值预警',text)


if __name__=='__main__':unittest.main()
