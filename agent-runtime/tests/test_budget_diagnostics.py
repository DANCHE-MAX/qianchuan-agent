"""Read-only plan diagnostics: real units, complete scope, missing data and no writes."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import budget_diagnostics as diag, listener, delivery
from agent.conversation import Conversation
from agent.reliability import ServiceError

STAMP = '2026-10-28T16:00:00.123456+08:00'
ENV = {'QC_ADVERTISER_IDS':'999', 'QC_ACCESS_TOKEN':'private-token', 'FEISHU_ALLOWED_CHAT_IDS':'oc_allowed'}


def item(pid='123', spend=450000000, orders=20):
    return {'ad_info':{'id':int(pid), 'name':'马甲 '+pid,'adlab_scene':'OVERALL_PROJECT',
                       'smart_bid_type':'SMART_BID_CUSTOM','status':'DELIVERY_OK','opt_status':'ENABLE'},
            'stats_info':{'stat_cost':spend,'total_pay_order_count_for_roi2':orders,
                          'total_prepay_and_pay_settle_overall_roi2_1h':5.8}}


def detail(pid='123', budget=5000, mode='BUDGET_MODE_DAY', status='DELIVERY_OK'):
    return {'code':0,'data':{'ad_id':int(pid), 'status':status,'opt_status':'ENABLE','adlab_scene':1,
            'delivery_setting':{'budget':budget,'budget_mode':mode,'roi2_goal':5,'overall_roi_cost_items':[3],
                                'smart_bid_type':'SMART_BID_CUSTOM'}}}


def page(rows, number=1, total=None, pages=None):
    return {'code':0, 'data':{'ad_list':rows,'page_info':{'page':number,'total_num':len(rows) if total is None else total,
                                                       'total_page':(1 if rows else 0) if pages is None else pages}}}


def performance(pids=('123',),spend='4500',orders='20',at=None):
    at=at or datetime.fromisoformat(STAMP)
    return {'state':'available','advertiser_id':'999','day':at.date().isoformat(),'unit':'CNY',
            'topic':'OVERALL_ROI_PRODUCT_PROJECT','cutoff':at.replace(microsecond=0).isoformat(),
            'fetched_at':at.isoformat(),'evidence_origin':'local_open_api_not_mcp','raw_pages':[],
            'rows':{pid:{'id':pid,'name':'马甲 '+pid,'ad_spend':spend,'order_count':orders,
                         'gmv':'30000','backend_net':'26100','comprehensive_cost':'4500'} for pid in pids}}


class Tests(unittest.TestCase):
    def analyze(self, row=None, payload=None, perf=None):
        return diag.analyze(row or item(), payload or detail(), '2026-10-28', STAMP, {},
                            performance=performance() if perf is None else perf,account='999')

    def test_unit_conversion_and_roi_no_unconfirmed_comparison(self):
        result = self.analyze()
        self.assertEqual(result['today_spend'], '4500')
        self.assertEqual(result['current_daily_budget_ratio'], '0.9')
        self.assertEqual(result['target_roi'], '5')
        self.assertIsNone(result['actual_comprehensive_roi'])
        self.assertEqual(result['report_ratios']['net_amount_div_comprehensive_cost'],'5.8')
        self.assertFalse(result['roi_target_comparable'])
        self.assertFalse(result['execution_allowed'])
        self.assertIn('观察候选', result['judgment'])

    def test_low_utilization_missing_samples_and_status_priority(self):
        self.assertIn('未接近', self.analyze(perf=performance(spend='10'))['judgment'])
        self.assertIn('样本不足', self.analyze(perf=performance(orders='1'))['judgment'])
        p = detail(status='OFFLINE_BALANCE')
        self.assertIn('状态限制',self.analyze(payload=p)['judgment'])
        p = detail(status='OFFLINE_BUDGET')
        self.assertIn('平台提示预算约束',self.analyze(payload=p)['judgment'])
        p['data']['opt_status'] = 'DISABLE'
        self.assertIn('未明确开启',self.analyze(payload=p)['judgment'])
        for metric in diag.budget_report.FIELDS:
            r=performance()
            del r['rows']['123'][metric]
            self.assertIn('资料不全', self.analyze(perf=r)['judgment'])
        self.assertIn('资料不全', self.analyze(payload=detail(pid='888'))['judgment'])

    def test_non_daily_and_invalid_values_never_become_zero(self):
        for mode in ('BUDGET_MODE_TOTAL','BUDGET_MODE_INFINITE'):
            p = self.analyze(payload=detail(mode=mode))
            self.assertIsNone(p['current_daily_budget_ratio'])
            self.assertIn('不适用',p['judgment'])
        p = self.analyze(payload=detail(budget=0))
        self.assertIsNone(p['current_daily_budget_ratio'])
        for value in (None,'NaN','Infinity',-1,True):
            p=self.analyze(perf=performance(spend=value))
            self.assertIsNone(p['today_spend'])
            self.assertIsNone(p['current_daily_budget_ratio'])

    def test_complete_pagination_both_strategies_no_old_report_dependency(self):
        request=Mock(side_effect=[page([item()],total=2,pages=2),page([item('124')],2,2,2),page([item('125')])])
        rows, raw=diag.list_plans(ENV,'2026-10-28',request)
        self.assertEqual(len(rows),3)
        self.assertEqual(len(raw),3)
        self.assertEqual(request.call_args_list[-1].args[0]['filtering']['smart_bid_type'],'SMART_BID_CONSERVATIVE')
        for call in request.call_args_list:
            params=call.args[0]
            self.assertEqual(params['adlab_scene'],'OVERALL_PROJECT')
            self.assertNotIn('having_cost',params['filtering'])
        request=Mock(side_effect=[page([]),page([])])
        self.assertEqual(diag.list_plans(ENV,'2026-10-28',request)[0],[])

    def test_duplicate_or_incomplete_pages_rejected(self):
        cases = [[page([item()],total=2,pages=2),page([item()],2,2,2)],
                 [page([item()],total=2,pages=1)],
                 [page([item()],total=2,pages=2),page([item('124')],2,3,2)],
                 [page([item()]),page([item()])]]
        for responses in cases:
            with self.assertRaises(ValueError):
                diag.list_plans(ENV,'2026-10-28',Mock(side_effect=responses))

    def test_detail_partial_failure_and_per_plan_scope(self):
        request=Mock(side_effect=[page([item(),item('124')]),page([])])
        def get(account,pid,token):
            if pid=='124':raise TimeoutError('private-token')
            return detail(pid)
        with patch.object(diag.budget_report,'fetch',return_value=performance(('123','124'))):
            s=diag.fetch(ENV,{},list_request=request,detail_request=get,
                         clock=lambda:datetime.fromisoformat(STAMP))
        self.assertEqual(len(s['plans']),2)
        self.assertIn('资料不全',s['plans'][1]['judgment'])
        self.assertFalse(s['is_complete_day'])
        self.assertEqual(s['performance_date'],'2026-10-28')

    def test_routing_native_pagination_memory_no_platform_writes_or_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config').mkdir()
            (root/'config/rules.json').write_text('{}',encoding='utf-8')
            memory=Conversation(root/'conversation.db')
            list_request=Mock(side_effect=[page([item(str(123+i)) for i in range(5)]),page([])])
            nf=Mock()
            ctx={'chat_id':'oc_allowed','sender_id':'u'}
            with patch.object(listener,'BASE',root), \
                 patch.object(diag,'_get',list_request), \
                 patch.object(diag.budgets,'_get',side_effect=lambda a,p,t:detail(p)), \
                 patch.object(diag.budget_report,'fetch',side_effect=lambda e,at,**kw:performance(tuple(str(123+i) for i in range(5)),at=at)), \
                 patch.object(listener,'Knowledge',side_effect=AssertionError('no LLM')), \
                 patch.object(listener,'_latest_report',side_effect=AssertionError('no old report')):
                listener.reply_command(nf,'message','预算诊断',env=ENV,context=ctx,memory=memory)
            self.assertEqual(nf.reply_card.call_count,2)
            text=json.dumps([c.args[1] for c in nf.reply_card.call_args_list],ensure_ascii=False)
            self.assertIn('4,500.00 元',text)
            self.assertIn('尚未完整',text)
            self.assertNotIn('**',text)
            self.assertNotIn('private-token',text)
            self.assertTrue(list((root/'data/qianchuan').glob('budget_diagnostics_*.json')))
            saved=memory.load('oc_allowed','u')[-1]
            self.assertIn('budget_diagnostics',saved['source'])
            self.assertNotIn('raw_list_pages',saved['source'])
            self.assertIsNone(saved['report_snapshot'])

    def test_invalid_authorization_one_safe_reply_no_old_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            nf=Mock()
            with patch.object(diag,'_get',return_value={'code':40105,'message':'private-token'}):
                diag.reply(nf,'message','预算诊断',tmp,ENV,{}, {'chat_id':'oc_allowed'})
            nf.reply_card.assert_called_once()
            text=json.dumps(nf.reply_card.call_args.args[1],ensure_ascii=False)
            self.assertIn('未完成',text)
            self.assertNotIn('private-token',text)
            self.assertFalse((Path(tmp)/'data').exists())
            nf.reset_mock()
            with patch.object(diag,'fetch',side_effect=AssertionError('not authorized')):
                diag.reply(nf,'message','预算诊断',tmp,ENV,{}, {'chat_id':'oc_other'})
            self.assertFalse(nf.method_calls)

    def test_command_matching_preserves_advice_questions(self):
        self.assertEqual(diag.term('预算诊断'), '')
        self.assertEqual(diag.term('预算诊断 马甲？'),'马甲')
        for q in ('如何诊断预算','预算','预算诊断怎么用','自动调预算','报告'):
            self.assertIsNone(diag.term(q))


if __name__=='__main__':unittest.main()

# budget-report-source-v32
