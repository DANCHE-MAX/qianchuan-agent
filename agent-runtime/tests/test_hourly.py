"""Hourly boundaries, exact values, restart recovery and current-vs-history routing."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import hourly as h, listener
from agent.knowledge import Knowledge


NOW = datetime(2026, 10, 28, 3, 12, tzinfo=h.CHINA)
ENV = {'QC_ADVERTISER_IDS': '999', 'QC_ACCESS_TOKEN': 'test-secret', 'KNOWLEDGE_ENABLED': 'true'}


def value(start, account='999', amount='1.25'):
    return {'advertiser_id': account, 'start': start.isoformat(),
            'end': (start+timedelta(hours=1)).isoformat(), 'fetched_at': NOW.isoformat(),
            'rows': [{'plan_id': '123', 'plan_name': '马甲',
                      **{key: amount for key in h.FIELDS}}], 'pages': []}


class Tests(unittest.TestCase):
    def test_hour_api_boundaries_paging_empty_and_invalid_rows(self):
        start = NOW.replace(hour=2, minute=0)
        def row(pid):
            return {'dimensions': {'ad_id': {'Value': pid}, 'ad_name': {'Value': '马甲'},
                    'stat_time_day': {'ValueStr': '2026-10-28'},
                    'stat_time_hour': {'ValueStr': '2026-10-28 02:00'}},
                    'metrics': {field: {'Value': '1.25'} for field in h.FIELDS.values()}}
        def page(rows, p=1, count=2, pages=2):
            return {'code': 0, 'data': {'rows': rows, 'page_info': {
                    'page': p, 'total_number': count, 'total_page': pages}}}
        request = Mock(side_effect=[page([row('123')]), page([row('456')], 2)])
        got = h.fetch_hour(ENV, start, now=NOW, request=request)
        self.assertEqual(len(got['rows']), 2)
        self.assertEqual(got['rows'][0]['ad_spend'], '1.25')
        self.assertEqual(request.call_args_list[0].args[0]['start_time'], '2026-10-28 02:00:00')
        self.assertEqual(request.call_args_list[0].args[0]['end_time'], '2026-10-28 02:59:59')
        self.assertEqual(h.fetch_hour(ENV, start, now=NOW, request=Mock(return_value=page([], count=0, pages=0)))['rows'], [])
        bad = row('123'); bad['dimensions']['stat_time_hour']['ValueStr'] = '2026-10-28 01:00'
        with self.assertRaises(ValueError): h.fetch_hour(ENV, start, now=NOW, request=Mock(return_value=page([bad],count=1,pages=1)))
        with self.assertRaises(ValueError): h.fetch_hour(ENV, start, now=NOW, request=Mock(return_value=page([row('123'),row('123')],pages=1)))
        with self.assertRaises(ValueError): h.fetch_hour(ENV, NOW.replace(minute=0), now=NOW, request=request)
        request = Mock(side_effect=[page([row('123')]), page([row('456')], 2, 3, 2)])
        with self.assertRaises(ValueError): h.fetch_hour(ENV, start, now=NOW, request=request)

    def test_restart_catchup_no_double_count_and_midnight(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(env, start, now=None): return value(start)
            with patch.object(h, 'fetch_hour', side_effect=fetch) as api, patch.object(h, 'fetch_plan_list', return_value=[]):
                h.Collector(tmp, ENV).tick(NOW)
                self.assertEqual(api.call_count, 3)
                h.Collector(tmp, ENV).tick(NOW)
                self.assertEqual(api.call_count, 3)
                context = json.loads(h.today_context(tmp, ENV, '现在如何', now=NOW))
                self.assertEqual(context['整体']['ad_spend'], '3.75')
                self.assertEqual(context['整体']['payment_roi'], '1')
                self.assertEqual(context['缺失小时'], [])
                midnight = NOW.replace(day=29, hour=0)
                h.Collector(tmp, ENV).tick(midnight)
                self.assertEqual(api.call_args.args[1].hour, 23)
                self.assertEqual(api.call_args.args[1].day, 28)
                self.assertIsNone(h.today_context(tmp, ENV, '现在', now=midnight))

    def test_failed_hour_is_gap_and_retried_without_blocking_newer_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(env, start, now=None):
                if start.hour == 1: raise TimeoutError('do not log secrets')
                return value(start)
            with patch.object(h,'fetch_hour', side_effect=fetch), patch.object(h,'fetch_plan_list',return_value=[]):
                with self.assertRaises(RuntimeError): h.Collector(tmp,ENV).tick(NOW)
            context=json.loads(h.today_context(tmp,ENV,'目前',now=NOW))
            self.assertEqual(context['整体']['ad_spend'],'2.50')
            self.assertEqual(context['缺失小时'],['2026-10-28T01:00:00+08:00'])
            with patch.object(h,'fetch_hour',side_effect=lambda env,start,now=None:value(start)) as api, patch.object(h,'fetch_plan_list',return_value=[]):
                h.Collector(tmp,ENV).tick(NOW)
                self.assertEqual(api.call_count,1)
            self.assertEqual(json.loads(h.today_context(tmp,ENV,'目前',now=NOW))['整体']['ad_spend'],'3.75')

    def test_current_routing_account_scope_and_no_yesterday_fallback(self):
        for text in ['目前如何','当前计划','现在给我简单点','今天消耗','今日表现']:
            self.assertTrue(h.is_current(text))
        for text in ['总结2026-10-27日报','现在查看昨天的日报','生成10.27报告','报告']:
            self.assertFalse(h.is_current(text))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); out=root/'out';out.mkdir();store=h.Store(root)
            store.save(value(NOW.replace(hour=0,minute=0),account='888',amount='987654'))
            kb=Mock(last_context=None)
            with patch.object(h,'now_china',return_value=NOW), patch.object(listener,'_latest_report',side_effect=AssertionError('must not load historical report')):
                self.assertIn('尚无',listener.answer('目前表现',out,ENV,kb));kb.ask.assert_not_called()
                store.save(value(NOW.replace(hour=0,minute=0)))
                kb.ask.return_value='小时数据回答'
                reply=listener.answer('现在表现',out,ENV,kb)
                self.assertIn('小时数据存在缺口',reply)
                self.assertNotIn('已采集1个完整小时，缺失2个小时',reply)
                payload=kb.last_context
                kb.ask.assert_not_called()
                self.assertNotIn('987654',payload)
                self.assertIn('1.25',payload)
                # "现在" takes priority over both quote and old conversation follow-up.
                nf=Mock()
                with patch.object(listener,'Knowledge',return_value=kb), patch.object(listener,'answer_chat',side_effect=AssertionError('old quote')):
                    listener.reply_command(nf,'msg','现在更简单点',out,ENV,{'chat_id':'c','sender_id':'u','parent_id':'old'})
                self.assertIn('截至',nf.reply_markdown.call_args.args[1])

    def test_full_domain_inventory_is_not_restricted_to_chuanfang(self):
        def item(pid,status='DELIVERY_OK',opt='ENABLE'):
            return {'ad_info':{'id':pid,'name':'计划'+pid,'status':status,'opt_status':opt,'adlab_scene':'UNI_PROJECT'}}
        def page(items,p=1,count=3,pages=2):
            return {'code':0,'data':{'ad_list':items,'page_info':{'page':p,'total_num':count,'total_page':pages}}}
        request=Mock(side_effect=[page([item('101'),item('102','EXTERNAL_URL_DISABLE')]),page([item('103','DISABLE','DISABLE')],2)])
        items=h.fetch_plan_list(ENV,'2026-10-28',request=request)
        self.assertEqual(len(items),3)
        params=request.call_args.args[0]
        self.assertEqual(params['marketing_goal'],'VIDEO_PROM_GOODS')
        self.assertNotIn('adlab_scene',params)
        self.assertNotIn('data_period',params)
        self.assertNotIn('smart_bid_type',params['filtering'])
        with tempfile.TemporaryDirectory() as tmp:
            store=h.Store(tmp);store.save(value(NOW.replace(hour=0,minute=0)))
            store.save_plans('999',items,NOW.isoformat())
            context=json.loads(h.today_context(tmp,ENV,'目前多少计划',now=NOW))
            self.assertEqual(context['状态列表计划总数'],3)
            self.assertEqual(context['状态列表在投数'],1)
            self.assertIn('商品视频全域',str(context['统计范围']))
        duplicate=Mock(return_value=page([item('101'),item('101')],count=2,pages=1))
        with self.assertRaises(ValueError): h.fetch_plan_list(ENV,'2026-10-28',request=duplicate)

    def test_current_evidence_label_and_sop_are_combined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'sop.md';p.write_text('预算按计划表现核对。',encoding='utf-8')
            kb=Knowledge(root/'kb.db');kb.ingest(p)
            class Response:
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def read(self): return json.dumps({'choices':[{'message':{'content':'这是已采集时段的数据。'}}]}).encode()
            with patch('urllib.request.urlopen',return_value=Response()) as api:
                result=kb.ask('目前预算',{'DEEPSEEK_API_KEY':'secret','DEEPSEEK_MODEL':'test'},report_context=json.dumps({'资料类型':'今日小时采集'}))
                self.assertIn('[1] 今日小时采集',result)
                self.assertIn('[2] sop.md',result)
                self.assertNotIn('本地模型日报',result)


if __name__ == '__main__': unittest.main()

# current-brief-v27
