import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import listener, notify

class Tests(unittest.TestCase):
    def setUp(self):
        # This suite covers the retained legacy mode; new daily mode has its own integration tests.
        mode = patch('agent.profit_analysis.enabled', return_value=False)
        mode.start()
        self.addCleanup(mode.stop)

    def test_routes(self):
        for text in ['达人视频审核不通过怎么办？', '计划审核不通过怎么办', '报告怎么看', '托管']:
            self.assertEqual(listener.command_route(text), 'knowledge')
        self.assertEqual(listener.command_route('计划 托管'), 'plan')
        self.assertEqual(listener.command_route('报告 秋冬'), 'report')
        self.assertEqual(listener.command_route('帮助'), 'help')
        self.assertIn('尚未开启', listener.answer('审核怎么办', env={}))
        self.assertNotIn('数据窗口', listener.answer('审核怎么办', env={}))

    def test_my_id_returns_only_the_author_without_llm(self):
        nf=Mock();env={'FEISHU_CHAT_ID':'oc_allowed'}
        context={'chat_id':'oc_allowed','sender_id':'ou_personal'}
        with patch.object(listener.Knowledge,'ask',side_effect=AssertionError('No LLM')):
            listener.reply_command(nf,'om_test','我的ID',env=env,context=context)
        self.assertIn('ou_personal',nf.reply_text.call_args.args[1])
        nf.reset_mock()
        listener.reply_command(nf,'om_test','我的ID',env=env,context={'chat_id':'oc_other','sender_id':'ou_other'})
        nf.reply_text.assert_not_called()

    def test_one_reply(self):
        nf = Mock()
        for question, method in [('审核怎么办', 'reply_markdown'), ('报告', 'reply_text')]:
            nf.reset_mock()
            with patch.object(listener, 'answer', return_value='**先申诉**') as answer:
                listener.reply_command(nf, 'om_test', question, env={})
                answer.assert_called_once()
            self.assertEqual(len(nf.method_calls), 1)
            getattr(nf, method).assert_called_once()

    def test_report_summary_context(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            report = {'audit': {'start':'2026-10-15','end':'2026-10-20','days':6},
                      'season':'autumn_winter', 'totals':{'gmv':'61489.32','predicted_profit':'2335.07'},
                      'plans':[], 'notes':['预计利润不是结算利润'], 'source':'private-path', 'assumptions':{'secret':'not-for-model'}}
            Path(tmp, 'model_test.json').write_text(json.dumps(report), encoding='utf-8')
            kb = Mock()
            kb.ask.return_value = '总结内容'
            env = {'KNOWLEDGE_ENABLED':'true'}
            result = listener.answer('为我简单总结日报中这六日的情况', tmp, env, kb)
            self.assertIn('2026-10-20', result)
            context = kb.ask.call_args.kwargs['report_context']
            self.assertIn('61489.32', context)
            self.assertNotIn('private-path', context)
            self.assertNotIn('not-for-model', context)
            kb.reset_mock()
            self.assertIn('不符', listener.answer('总结2026-10-25日报', tmp, env, kb))
            kb.ask.assert_not_called()

    def test_chat_quote_and_scope(self):
        nf = Mock()
        item = {'chat_id':'oc_a','message_id':'om_old','create_time':'1000','msg_type':'interactive',
                'body':{'content':json.dumps({'elements':[{'text':{'content':'千川投流日报 毛成交100元'}}]})}}
        nf.get_message.return_value = [item]
        context = {'chat_id':'oc_a','parent_id':'om_old'}
        with patch.object(listener.Knowledge, 'ask', return_value='分析') as ask:
            result = listener.answer_chat(nf, '总结', {'KNOWLEDGE_ENABLED':'true'}, context)
            self.assertIn('被引用', result)
            self.assertIn('毛成交100元', ask.call_args.kwargs['report_context'])
            ask.reset_mock()
            nf.get_message.return_value = [dict(item, chat_id='oc_other')]
            self.assertIn('未能', listener.answer_chat(nf, '总结', {'KNOWLEDGE_ENABLED':'true'}, context))
            ask.assert_not_called()
        nf.get_message.side_effect = RuntimeError('private-token')
        result = listener.answer_chat(nf, '总结', {'KNOWLEDGE_ENABLED':'true'}, context)
        self.assertIn('读取群消息失败', result)
        self.assertNotIn('private-token', result)

    def test_card_payload(self):
        nf = notify.FeishuAppNotifier('app','secret','oc_unused')
        nf._tenant_token = lambda: 'token'
        nf._request = Mock(return_value={'code':0})
        nf.reply_markdown('om_test', '**先申诉**\n1. 步骤[1]')
        args = nf._request.call_args.args
        self.assertTrue(args[1].endswith('/im/v1/messages/om_test/reply'))
        self.assertEqual(args[2]['msg_type'], 'interactive')
        card = json.loads(args[2]['content'])
        self.assertEqual(card['elements'][0]['text']['tag'], 'plain_text')
        self.assertIn('先申诉', card['elements'][0]['text']['content'])
        self.assertNotIn('**', card['elements'][0]['text']['content'])

if __name__ == '__main__': unittest.main()
