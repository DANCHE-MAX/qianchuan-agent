"""Inbound generation, publication, scope, restart/retry and yesterday semantics."""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import generation as g, qianchuan, budgets, settings, listener, notify
from agent.conversation import Conversation

PID = '9000000000000008'


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'out'
        self.out.mkdir()
        (self.root/'config').mkdir()
        self.cfg = settings.load_config()
        (self.root/'config/rules.json').write_text(json.dumps(self.cfg), encoding='utf-8')
        self.env = {'QC_ADVERTISER_IDS': '999', 'QC_ACCESS_TOKEN': 'secret-token',
                    'FEISHU_ALLOWED_CHAT_IDS': 'oc_allowed', 'FEISHU_CHAT_ID': 'oc_default'}
        self.context = {'chat_id':'oc_allowed', 'sender_id':'u',
                        'create_time': str(int(datetime(2026,10,26,0,30,tzinfo=g.CHINA).timestamp()*1000))}
        self.clock = patch.object(g.model, 'china_today', return_value=date(2026,10,26))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def api(self, params, token):
        self.assertEqual(params['start_time'], '2026-10-25 00:00:00')
        self.assertEqual(params['end_time'], '2026-10-25 23:59:59')
        return {'code':0, 'data':{'page_info':{'page':1,'total_number':1,'total_page':1}, 'rows':[
            {'dimensions':{'ad_id':{'Value':PID}, 'ad_name':{'Value':'马甲'},
                           'stat_time_day':{'ValueStr':'2026-10-25'}},
             'metrics':{field:{'Value':value} for field,value in zip(qianchuan.METRICS.values(),[100,1000,800,110])}}]}}

    def budget(self, account, pid, token):
        return {'code':0,'data':{'ad_id':pid,'delivery_setting':{'budget':5000,'budget_mode':'BUDGET_MODE_DAY'}}}

    def test_real_pipeline_saves_and_replies_only_to_origin_without_llm(self):
        nf = Mock()
        with patch.object(listener, 'BASE', self.root), \
             patch.object(qianchuan, '_get', side_effect=self.api), \
             patch.object(budgets, '_get', side_effect=self.budget), \
             patch.object(listener.Knowledge, 'ask', side_effect=AssertionError('No LLM')):
            listener.reply_command(nf, 'om_1', '生成昨日日报', outdir=self.out, env=self.env, context=self.context)
        nf.send_card.assert_not_called()
        self.assertEqual(nf.reply_card.call_count, 2)
        for call in nf.reply_card.call_args_list:
            self.assertEqual(call.args[0], 'om_1')
            self.assertTrue(call.kwargs['request_uuid'])
        r = listener._latest_report(self.out)
        self.assertEqual(r['audit']['days'], 1)
        self.assertEqual(r['audit']['end'], '2026-10-25')
        self.assertEqual(r['totals']['gmv'], '1000')
        self.assertEqual(r['plans'][0]['configured_budget'], '5000')
        self.assertEqual(len(list((self.root/'data/qianchuan').glob('*.csv'))), 1)
        self.assertEqual(len(list((self.root/'data/qianchuan').glob('*.api.json'))), 1)
        self.assertEqual(len(list(self.out.glob('*_cards.json'))), 1)
        self.assertNotIn('secret-token', ''.join(p.read_text(encoding='utf-8') for p in self.out.glob('*')))
        turns = Conversation(self.root/'data/conversation.db').load('oc_allowed','u')
        self.assertEqual(turns[-1]['report_snapshot']['audit']['end'], '2026-10-25')

    def test_failure_never_returns_or_overwrites_old_report(self):
        old = self.out/'model_old.json'
        old.write_text('{"old":"do not replace"}', encoding='utf-8')
        nf = Mock()
        with patch.object(qianchuan, '_get', side_effect=RuntimeError('secret-token')):
            g.reply(nf,'om_fail','生成昨日日报',self.root,self.out,self.env,self.context)
        self.assertEqual(old.read_text(encoding='utf-8'), '{"old":"do not replace"}')
        self.assertEqual(len(list(self.out.iterdir())),1)
        text = json.dumps(nf.reply_card.call_args.args[1],ensure_ascii=False)
        self.assertIn('生成失败', text)
        self.assertNotIn('secret-token', text)
        self.assertNotIn('secret-token', (self.root/'data/generation.db').read_bytes().decode('utf-8',errors='ignore'))

    def test_partial_send_retry_reuses_payload_and_does_not_repeat_earlier_cards(self):
        nf = Mock()
        nf.reply_card.side_effect = [None, RuntimeError('network')]
        with patch.object(qianchuan, '_get', side_effect=self.api) as fetch, \
             patch.object(budgets, '_get', side_effect=self.budget):
            with self.assertRaises(RuntimeError):
                g.reply(nf,'om_retry','生成昨日日报',self.root,self.out,self.env,self.context)
            first_uuid = nf.reply_card.call_args_list[0].kwargs['request_uuid']
            failed_uuid = nf.reply_card.call_args_list[1].kwargs['request_uuid']
            nf.reset_mock()
            nf.reply_card.side_effect = None
            with patch.object(g.model,'china_today',return_value=date(2026,10,27)):
                result = g.reply(nf,'om_retry','生成昨日日报',self.root,self.out,self.env,self.context)
            fetch.assert_called_once()
            nf.reply_card.assert_called_once()
            self.assertEqual(nf.reply_card.call_args.kwargs['request_uuid'],failed_uuid)
            self.assertNotEqual(first_uuid,failed_uuid)
            self.assertEqual(result['audit']['end'],'2026-10-25')
            nf.reset_mock()
            g.reply(nf,'om_retry','生成昨日日报',self.root,self.out,self.env,self.context)
            nf.reply_card.assert_not_called()

    def test_authorization_and_explicit_commands(self):
        nf = Mock()
        with patch.object(g,'generate',side_effect=AssertionError('not authorized')):
            g.reply(nf,'om_bad','生成昨日日报',self.root,self.out,self.env,dict(self.context,chat_id='oc_other'))
        self.assertEqual(nf.method_calls,[])
        self.assertEqual(g.target_date(self.context),'2026-10-25')
        for command in ('生成昨日日报','生成昨天的日报','生成日报'):
            self.assertTrue(g.matches(command))
        for command in ('报告','总结日报','如何生成昨日日报','知识库说“生成昨日日报”'):
            self.assertFalse(g.matches(command))

    def test_custom_dates_formats_invalid_dates_and_cross_year(self):
        for command in ('为我生成10.22日日报', '生成10.22日报', '请帮我生成10月22日的日报',
                        '生成2026-10-22日报', '生成2026/10/22日报', '生成2026年10月22号日报'):
            with self.subTest(command=command):
                self.assertTrue(g.matches(command))
                self.assertEqual(g.target_date(self.context,command),'2026-10-22')
        self.assertEqual(g.target_date(self.context,'生成前天日报'),'2026-10-24')
        self.assertEqual(g.target_date(self.context,'生成2024-02-29日报'),'2024-02-29')
        for command in ('生成2.30日报','生成2025-02-29日报','生成13.1日报','生成2026-00-10日报'):
            self.assertTrue(g.matches(command))
            with self.assertRaisesRegex(ValueError,'日期不存在'):
                g.target_date(self.context,command)
        for command in ('生成今日日报','生成10.26日报','生成10.27日报','生成明天日报','生成12.31日报'):
            with self.assertRaisesRegex(ValueError,'当天或未来'):
                g.target_date(self.context,command)
        january = dict(self.context,create_time=str(int(datetime(2026,1,2,tzinfo=g.CHINA).timestamp()*1000)))
        with self.assertRaisesRegex(ValueError,'跨年'):
            g.target_date(january,'生成12.31日报')
        self.assertEqual(g.target_date(january,'生成2025-12-31日报'),'2025-12-31')
        self.assertFalse(g.matches('如何生成10.22日报'))
        self.assertFalse(g.matches('生成10.22至10.25日报'))

    def test_report_alias_enters_generation_and_keeps_current_account(self):
        g.model.china_today.return_value = date(2026,10,28)
        ctx = dict(self.context, create_time=str(int(datetime(2026,10,28,tzinfo=g.CHINA).timestamp()*1000)))
        for command in ('生成10-27报告', '生成10.27日报', '生成2026-10-27报告', '请帮我生成10月27日的报告'):
            self.assertTrue(g.matches(command), command)
            self.assertEqual(g.target_date(ctx,command),'2026-10-27')
        for question in ('如何生成10-27报告', '生成10-27报告需要什么数据', '总结10-27报告'):
            self.assertFalse(g.matches(question), question)
        report = {'audit':{'start':'2026-10-27','end':'2026-10-27','days':1},
                  'source_api':{'advertiser_id':'999'},'totals':{},'plans':[],'notes':[]}
        built = [{'header':{'title':{'content':'2026-10-27'}}}]
        nf = Mock()
        with patch.object(listener,'BASE',self.root), \
             patch.object(g,'generate',return_value=(report,built)) as generate, \
             patch.object(listener,'Knowledge',side_effect=AssertionError('must not enter knowledge')):
            listener.reply_command(nf,'om_report_alias','生成10-27报告',outdir=self.out,env=self.env,context=ctx)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[3]['QC_ADVERTISER_IDS'],'999')
        self.assertEqual(generate.call_args.args[-1],'2026-10-27')
        nf.reply_card.assert_called_once()

    def test_large_account_preserves_full_model_but_bounds_budget_and_cards(self):
        from decimal import Decimal
        from agent import cards
        many = [{'plan_id':str(1000+i), 'plan_name':'plan'+str(i), 'gmv':Decimal(100),
                 'ad_spend':Decimal(30), 'backend_net':Decimal(80), 'comprehensive_cost':Decimal(30)} for i in range(31)]
        many += [{'plan_id':'9999','plan_name':'zero','gmv':Decimal(0),'ad_spend':Decimal(0)}]
        report = {'season':'autumn_winter','audit':{'start':'2026-10-25','end':'2026-10-25','days':1},
                  'totals':{},'plans':many,'notes':[]}
        built=cards.build(report,active_only=True,max_plans=20)
        self.assertEqual(len(built),6)
        self.assertEqual(len(report['plans']),32)
        text=json.dumps(built,ensure_ascii=False)
        self.assertIn('完整报告保留32条计划',text)
        self.assertIn('plan19',text)
        self.assertNotIn('plan20',text)
        self.assertNotIn('9999',text)

    def test_invalid_date_replies_once_without_api_or_generation(self):
        nf = Mock()
        with patch.object(g,'generate',side_effect=AssertionError('invalid date')) as run:
            g.reply(nf,'om_invalid','为我生成2.30日日报',self.root,self.out,self.env,self.context)
            g.reply(nf,'om_invalid','为我生成2.30日日报',self.root,self.out,self.env,self.context)
            run.assert_not_called()
        nf.reply_card.assert_called_once()
        self.assertIn('日期不存在',json.dumps(nf.reply_card.call_args.args[1],ensure_ascii=False))
        self.assertEqual(list(self.out.iterdir()),[])

    def test_custom_date_router_and_retry_preserve_requested_day(self):
        nf = Mock()
        requested = '2026-10-22'
        r = {'audit':{'start':requested,'end':requested,'days':1}, 'totals':{},'plans':[],'notes':[]}
        newer = dict(r,audit={'start':'2026-10-25','end':'2026-10-25','days':1})
        (self.out/'model_newer.json').write_text(json.dumps(newer),encoding='utf-8')
        built = [{'header':{'title':{'content':requested}}}]
        with patch.object(listener,'BASE',self.root), patch.object(g,'generate',return_value=(r,built)) as run:
            nf.reply_card.side_effect = RuntimeError('delivery failed')
            with self.assertRaises(RuntimeError):
                listener.reply_command(nf,'om_date','为我生成10.22日日报',outdir=self.out,env=self.env,context=self.context)
            self.assertEqual(run.call_args.args[-1],requested)
            nf.reply_card.side_effect = None
            with patch.object(g.model,'china_today',return_value=date(2027,1,1)):
                listener.reply_command(nf,'om_date','为我生成10.22日日报',outdir=self.out,env=self.env,context=self.context)
            run.assert_called_once()
        self.assertIn(requested,json.dumps(nf.reply_card.call_args.args[1]))
        self.assertEqual(listener._latest_report(self.out)['audit']['end'],'2026-10-25')
        turns = Conversation(self.root/'data/conversation.db').load('oc_allowed','u')
        self.assertEqual(turns[-1]['report_snapshot']['audit']['end'],requested)

    def test_inbox_publishes_context_with_message_and_preserves_duplicate_scope(self):
        inbox = listener.Inbox(self.root/'inbox.db')
        self.assertTrue(inbox.add('om_atomic','生成昨日日报',context=self.context))
        self.assertEqual(inbox.next()[0],'om_atomic')
        self.assertEqual(inbox.get_context('om_atomic'),self.context)
        self.assertFalse(inbox.add('om_atomic','生成昨日日报',context={'chat_id':'oc_other'}))
        self.assertEqual(inbox.get_context('om_atomic'),self.context)

    def test_project_lock_and_reply_uuid_transport(self):
        path = self.root/'data/generation.lock'
        with g.generation_lock(path):
            with self.assertRaises(OSError):
                with g.generation_lock(path):
                    pass
        with g.generation_lock(path):
            pass
        nf = notify.FeishuAppNotifier('app','secret','oc_default')
        nf._tenant_token = lambda:'fake'
        nf._request = Mock(return_value={'code':0})
        nf.reply_card('om_source',{'elements':[]},request_uuid='unique-page')
        call = nf._request.call_args
        self.assertTrue(call.args[1].endswith('/om_source/reply'))
        self.assertEqual(call.args[2]['uuid'],'unique-page')
        self.assertNotIn('receive_id',call.args[2])


if __name__ == '__main__':
    unittest.main()
