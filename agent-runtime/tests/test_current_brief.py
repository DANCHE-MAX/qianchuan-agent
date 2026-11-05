"""Formatting regressions: concise defaults, detailed opt-in and safety kept."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import current_brief as b, listener as l, data_quality as q

ENV={'QC_ADVERTISER_IDS':'9000000000000099','KNOWLEDGE_ENABLED':'true'}
INFO={'资料类型':'今日半小时采集','日期':'2026-10-29','账户':'9000000000000099',
      '最新采集时间':'2026-10-29T14:30:37+08:00','数据截止时间':'2026-10-29T14:30:35+08:00',
      '整体':{'ad_spend':'251.93','gmv':'2272','payment_roi':'9.02'},
      '异常分析准入':{'state':'blocked','reasons':['gap'],'cutoff':'2026-10-29T14:30:35+08:00'},
      '分析限制':'暂无法判断：半小时快照不连续或间隔不在25—40分钟内。',
      '数据完整性检查':{'invalid_records':0,'covered_hours':14,'expected_hours':14,'missing_hours':[],
        'snapshot_cutoff':'2026-10-29T14:30:35+08:00','historical_missing_slots':[],
        'operational_missing_slots':['x']*21,'paused_missing_slots':[],
        'coverage_registered':True,'components':{'hourly':'complete','intervals':'incomplete','snapshot':'fresh'},
        'scope':'经营与状态范围分开核对。'}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.kb=Mock()
        self.kb.ask.return_value='依据累计快照及监测资料（以采集时间和范围为准）。\n今日截至14:30，消耗251.93元。[1]\n\n参考资料：\n[1] 今日半小时采集'
        self.net=patch('urllib.request.urlopen',side_effect=AssertionError('No real network'))
        self.net.start();self.addCleanup(self.net.stop)

    def answer(self,question,info=None):
        value=info or INFO
        with patch.object(l.hourly,'today_context',return_value=json.dumps(value)),patch.object(q,'current',return_value=value['数据完整性检查']):
            return l.answer_current(question,self.root/'out',ENV,self.kb)

    def test_overview_has_three_numbers_without_diagnostics(self):
        text=self.answer('目前情况')
        self.assertIn('消耗251.93元，成交2,272.00元，整体支付ROI9.02',text)
        for removed in ('账户186236','14/14','21个','T14:30','小时报表','依据累计','[1]'):self.assertNotIn(removed,text)
        self.assertIn('不连续',text);self.assertLess(len(text),130);self.kb.ask.assert_not_called()

    def test_spend_query_only_answers_spend(self):
        text=self.answer('目前消耗是多少');self.assertIn('消耗251.93元',text)
        self.assertNotIn('成交2,272',text);self.assertIn('尚不能判断',text)

    def test_direct_numbers_keep_source_for_followup(self):
        self.answer('目前情况')
        context=json.loads(self.kb.last_context)
        self.assertEqual(context[0]['文档'],'今日半小时采集')
        source=json.loads(context[0]['内容']);self.assertEqual(source['整体']['ad_spend'],'251.93')

    def test_roi_uses_gross_over_spend(self):
        value=copy.deepcopy(INFO);value['整体']['payment_roi']='999'
        self.assertIn('整体支付ROI9.02',self.answer('当前ROI',value))

    def test_zero_spend_is_no_roi_not_infinity(self):
        value=copy.deepcopy(INFO);value['整体']['ad_spend']='0'
        self.assertIn('整体支付ROI暂无',self.answer('当前ROI',value))

    def test_detailed_request_retains_full_metadata(self):
        text=self.answer('目前情况详细展开')
        self.assertIn('小时报表：完整',text);self.assertIn('运行期间漏采21个',text)
        self.assertIn('账户9000000000000099',text);self.kb.ask.assert_called_once()

    def test_explicit_gap_question_is_detailed(self):
        self.assertTrue(b.detailed('今天缺少多少个观察点'))
        self.assertTrue(b.detailed('目前缺口情况'))

    def test_scoped_plan_query_is_not_replaced_with_account_totals(self):
        self.assertIsNone(b.direct('目前马甲消耗是多少',INFO))
        self.assertIsNone(b.direct('当前123计划ROI',INFO))

    def test_empty_report_does_not_invent_zero_spend(self):
        value=copy.deepcopy(INFO);value['异常分析准入']['state']='empty'
        text=self.answer('目前消耗是多少',value)
        self.assertIn('未返回经营记录',text);self.assertNotIn('消耗251.93',text);self.assertNotIn('消耗0',text)

    def test_stale_warning_remains_in_short_reply(self):
        value=copy.deepcopy(INFO);value['异常分析准入']['reasons']=['stale']
        self.assertIn('快照已过期',self.answer('目前表现',value))

    def test_ready_does_not_assert_no_anomaly(self):
        value=copy.deepcopy(INFO);value['异常分析准入'].update(state='ready',reasons=[])
        text=self.answer('目前情况',value)
        self.assertNotIn('没有异常',text);self.assertNotIn('一切正常',text)

    def test_false_all_clear_llm_uses_compact_grounded_fallback(self):
        self.kb.ask.return_value='一切正常，没有异常。'
        text=self.answer('当前能判断投放异常吗')
        self.assertIn('消耗251.93元',text);self.assertIn('尚不能判断',text)
        self.assertNotIn('一切正常',text);self.assertNotIn('T14:30',text)

    def test_single_current_source_boilerplate_is_removed(self):
        text=self.answer('当前计划表现请简单说')
        self.assertNotIn('依据累计',text);self.assertNotIn('参考资料',text);self.assertNotIn('[1]',text)
        self.assertIn('不连续',text)

    def test_sop_sources_are_preserved(self):
        text=b.clean_generated('依据累计快照及监测资料（以采集时间和范围为准）。\n核对资质[2]。\n\n参考资料：\n[1] 今日半小时采集\n[2] 商城SOP · 段2')
        self.assertIn('[2]',text);self.assertIn('商城SOP',text);self.assertIn('来源：',text)

    def test_existing_limit_is_not_repeated(self):
        self.kb.ask.return_value='截至14:30，样本不足，暂无法判断趋势。'
        text=self.answer('当前能判断趋势吗')
        self.assertEqual(text.count('暂无法判断'),1)

    def test_invalid_data_remains_blocked(self):
        value=copy.deepcopy(INFO);value['数据完整性检查']['invalid_records']=1
        text=self.answer('目前消耗是多少',value)
        self.assertIn('未通过',text);self.kb.ask.assert_not_called()

    def test_hourly_gaps_remain_visible_without_counts(self):
        value=copy.deepcopy(INFO);value['资料类型']='今日小时采集';value.pop('异常分析准入')
        value.pop('数据截止时间');value['覆盖小时']=[{'end':'2026-10-29T13:00:00+08:00'}]
        value['缺失小时']=['2026-10-29T10:00:00+08:00']
        text=self.answer('目前情况',value)
        self.assertIn('截至13:00',text);self.assertIn('汇总并不完整',text)

    def test_partial_limit_stays_short(self):
        value=copy.deepcopy(INFO);value['异常分析准入']['state']='partial'
        text=self.answer('目前表现',value)
        self.assertIn('部分计划或指标',text);self.assertNotIn('21个',text)

    def test_operating_subscope_kept_in_short_stats(self):
        value=copy.deepcopy(INFO);value['统计范围']={'经营':'乘方商品计划累计报表','状态':'商品视频全域'}
        text=self.answer('目前情况',value)
        self.assertIn('乘方计划消耗251.93元',text)
        self.assertNotIn('全账户消耗',text)


if __name__=='__main__':unittest.main()
