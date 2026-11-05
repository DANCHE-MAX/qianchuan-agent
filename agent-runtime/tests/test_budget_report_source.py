import copy
from datetime import datetime
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import io
import tempfile
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import budget_diagnostics as diag, hourly, monitor
import test_budget_diagnostics as fixtures

AT=datetime.fromisoformat(fixtures.STAMP)
ENV=fixtures.ENV


def payload(rows=None,**values):
    items=[]
    for pid in rows or ['123']:
        amounts={'ad_spend':'4500','gmv':'9000','backend_net':'6000','comprehensive_cost':'5000','order_count':'20',**values}
        items.append({'dimensions':{'ad_id':{'Value':int(pid)},'ad_name':{'Value':'马甲 '+pid},
                                    'stat_time_day':{'ValueStr':'2026-10-28'}},
                      'metrics':{field:{'Value':amounts[key]} for key,field in monitor.METRICS.items()}})
    return {'code':0,'data':{'rows':items,'page_info':{'page':1,'total_number':len(items),'total_page':1}}}


def performance(rows=None,**values):
    amounts={'ad_spend':'4500','gmv':'9000','backend_net':'6000','comprehensive_cost':'5000','order_count':'20',**values}
    return {'state':'available','advertiser_id':'999','day':'2026-10-28','topic':'OVERALL_ROI_PRODUCT_PROJECT',
            'unit':'CNY','cutoff':AT.replace(microsecond=0).isoformat(),'fetched_at':AT.isoformat(),
            'rows':{pid:{'id':pid,'name':'马甲 '+pid,**amounts} for pid in rows or ['123']},
            'evidence_origin':'local_open_api_not_mcp','raw_pages':[]}


class Tests(unittest.TestCase):
    def source(self):
        self.assertIsNotNone(importlib.util.find_spec('agent.budget_report'),'独立报表来源尚未实现')
        return importlib.import_module('agent.budget_report')

    def test_legacy_stats_are_not_used(self):
        result=diag.analyze(fixtures.item(),fixtures.detail(),'2026-10-28',AT.isoformat(),{})
        self.assertIsNone(result['today_spend'])
        self.assertIsNone(result['actual_comprehensive_roi'])

    def test_list_sends_only_inventory_parameters(self):
        request=Mock(side_effect=[fixtures.page([fixtures.item()]),fixtures.page([])])
        diag.list_plans(ENV,'2026-10-28',request)
        for call in request.call_args_list:
            for key in ('start_time','end_time','fields','data_period','order_type','order_field'):
                self.assertNotIn(key,call.args[0])
            self.assertNotIn('having_cost',call.args[0]['filtering'])

    def test_full_inventory_drops_legacy_dates(self):
        request=Mock(return_value=fixtures.page([fixtures.item()]))
        hourly.fetch_plan_list(ENV,'2026-10-28',request)
        # t80: the API now requires a same-day start/end pair (40000 when missing).
        params=request.call_args.args[0]
        self.assertEqual((params['start_time'],params['end_time']),('2026-10-28 00:00:00','2026-10-28 23:59:59'))
        for key in ('fields','data_period'):self.assertNotIn(key,params)

    def test_report_yuan_not_divided_and_three_ratios(self):
        p=diag.analyze(fixtures.item(),fixtures.detail(),'2026-10-28',AT.isoformat(),{},performance=performance())
        self.assertEqual(p['today_spend'],'4500')
        self.assertEqual(p['report_ratios']['payment_roi'],'2')
        self.assertEqual(p['report_ratios']['net_amount_div_ad_spend'],'1.333333333333333333333333333')
        self.assertEqual(p['report_ratios']['net_amount_div_comprehensive_cost'],'1.2')
        self.assertIsNone(p['actual_comprehensive_roi'])
        self.assertFalse(p['roi_target_comparable'])

    def test_absent_plan_is_missing_not_zero(self):
        p=diag.analyze(fixtures.item(),fixtures.detail(),'2026-10-28',AT.isoformat(),{},performance=performance(['999']))
        self.assertIsNone(p['today_spend']);self.assertIsNone(p['current_daily_budget_ratio'])
        self.assertEqual(p['performance_status'],'no_record')

    def test_zero_denominators_and_missing_settings_distinct(self):
        p=diag.analyze(fixtures.item(),fixtures.detail(),'2026-10-28',AT.isoformat(),{},
                       performance=performance(ad_spend='0',comprehensive_cost='0'))
        self.assertEqual(p['today_spend'],'0');self.assertIsNone(p['report_ratios']['payment_roi'])
        self.assertIsNone(p['report_ratios']['net_amount_div_comprehensive_cost'])
        raw=fixtures.detail();raw['data']['delivery_setting']['alliance_commision_switch']=False
        p=diag.analyze(fixtures.item(),raw,'2026-10-28',AT.isoformat(),{},performance=performance())
        switches=p['roi_field_evidence']['settings']
        self.assertEqual(switches['alliance_commision_switch'],{'present':True,'value':False})
        self.assertEqual(switches['star_task_material_switch'],{'present':False,'value':None})

    def test_report_collector_validates_pages_and_units(self):
        source=self.source();request=Mock(return_value=payload())
        with patch.object(hourly,'now_china',return_value=AT):
            result=source.fetch(ENV,AT,request=request,clock=lambda:AT)
        self.assertEqual(result['rows']['123']['ad_spend'],'4500')
        self.assertEqual(result['unit'],'CNY')
        self.assertEqual(result['evidence_origin'],'local_open_api_not_mcp')
        self.assertEqual(request.call_args.args[0]['advertiser_id'],999)

    def test_report_failure_still_shows_current_budget(self):
        self.source()
        request=Mock(side_effect=[fixtures.page([fixtures.item()]),fixtures.page([])])
        with patch.object(hourly,'now_china',return_value=AT):
            result=diag.fetch(ENV,list_request=request,detail_request=lambda a,p,t:fixtures.detail(p),
                              report_request=Mock(side_effect=TimeoutError('private-token')),clock=lambda:AT)
        self.assertEqual(result['plans'][0]['budget_snapshot']['amount'],'5000')
        self.assertIsNone(result['plans'][0]['today_spend'])
        self.assertEqual(result['report_source']['state'],'unavailable')
        self.assertNotIn('private-token',json.dumps(result,ensure_ascii=False))

    def test_report_exact_id_join_and_single_query(self):
        self.source()
        request=Mock(side_effect=[fixtures.page([fixtures.item('124'),fixtures.item('123')]),fixtures.page([])])
        report=Mock(return_value=payload(['123']))
        with patch.object(hourly,'now_china',return_value=AT):
            result=diag.fetch(ENV,list_request=request,detail_request=lambda a,p,t:fixtures.detail(p),
                              report_request=report,clock=lambda:AT)
        report.assert_called_once();self.assertIsNone(result['plans'][0]['today_spend'])
        self.assertEqual(result['plans'][1]['today_spend'],'4500')
        self.assertFalse(result['execution_allowed'])

    def test_invalid_report_rejected_no_partial_rows(self):
        source=self.source()
        for field,value in (('ad_spend','NaN'),('order_count','1.2'),('gmv','-1')):
            with self.subTest(field=field),patch.object(hourly,'now_china',return_value=AT),self.assertRaises(ValueError):
                source.fetch(ENV,AT,request=Mock(return_value=payload(**{field:value})),clock=lambda:AT)

    def test_backend_error_code_not_accepted_with_data(self):
        source=self.source();bad=payload();bad['code']=40002
        with patch.object(hourly,'now_china',return_value=AT),self.assertRaises(ValueError):
            source.fetch(ENV,AT,request=Mock(return_value=bad),clock=lambda:AT)

    def test_empty_report_has_no_plan_zero_fabrication(self):
        source=self.source();empty={'code':0,'data':{'rows':[],'page_info':{'page':1,'total_number':0,'total_page':0}}}
        with patch.object(hourly,'now_china',return_value=AT):
            result=source.fetch(ENV,AT,request=Mock(return_value=empty),clock=lambda:AT)
        self.assertEqual(result['rows'],{});self.assertEqual(result['state'],'empty')

    def test_naive_clock_rejected(self):
        self.source()
        with self.assertRaises(ValueError):diag.fetch(ENV,clock=lambda:AT.replace(tzinfo=None))

    def test_wrong_account_performance_not_used(self):
        perf=performance();perf['advertiser_id']='888'
        p=diag.analyze(fixtures.item(),fixtures.detail(),'2026-10-28',AT.isoformat(),{},performance=perf,account='999')
        self.assertIsNone(p['today_spend'])

    def test_malformed_detail_does_not_discard_report_or_crash(self):
        p=diag.analyze(fixtures.item(),{'code':0,'data':[{}]},'2026-10-28',AT.isoformat(),{},performance=performance(),account='999')
        self.assertEqual(p['today_spend'],'4500')
        self.assertEqual(p['budget_snapshot']['status'],'unavailable')

    def test_past_or_future_report_cannot_make_current_ratio(self):
        for delta in (-50,3):
            from datetime import timedelta
            data=performance();data['cutoff']=(AT+timedelta(minutes=delta)).isoformat()
            p=diag.analyze(fixtures.item(),fixtures.detail(),'2026-10-28',AT.isoformat(),{},performance=data)
            self.assertIsNone(p['current_daily_budget_ratio'])

    def test_duplicate_report_page_rejected(self):
        source=self.source();bad=payload(['123','123'])
        with patch.object(hourly,'now_china',return_value=AT),self.assertRaises(ValueError):
            source.fetch(ENV,AT,request=Mock(return_value=bad),clock=lambda:AT)

    def test_cli_missing_plan_report_is_partial_failure(self):
        path=Path(__file__).resolve().parents[1]/'tools/check_budget_report.py'
        spec=importlib.util.spec_from_file_location('budget_cli',path)
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        p=diag.analyze(fixtures.item(),fixtures.detail(),'2026-10-28',AT.isoformat(),{},performance=performance(['999']))
        snapshot={'advertiser_id':'999','evidence_origin':'local_open_api_not_mcp','report_source':{'state':'available'},
                  'plans':[p],'total_plans_in_scope':1,'fetched_at':AT.isoformat()}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sys,'argv',[str(path),'--project',tmp,'--account','999']), \
             patch.object(cli.settings,'load_env',return_value=ENV), \
             patch.object(cli.settings,'load_config',return_value={}), \
             patch.object(cli.budget_diagnostics,'fetch',return_value=snapshot), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(),1)

if __name__=='__main__':unittest.main()
