"""Validate native card presentation, source amounts and conversation snapshots."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import card_view, cards, listener, notify
from agent.conversation import Conversation


def sample():
    return {'audit': {'start': '2026-10-15', 'end': '2026-10-20', 'days': 6, 'selected_rows': 43},
            'season': 'autumn_winter',
            'totals': {'gmv': '61489.32', 'ad_spend': '8367.37', 'comprehensive_roi': '5.7431',
                       'predicted_profit': '2335.07', 'predicted_net': None},
            'plans': [{'plan_id': '9000000000000006', 'plan_name': '托管', 'gmv': '31242.08',
                       'ad_spend': '4562.86', 'comprehensive_roi': '5.67', 'predicted_profit': '-52.06'}]}


def contents(value):
    if isinstance(value, dict):
        if value.get('tag') == 'plain_text':
            yield value['content']
        for item in value.values():
            yield from contents(item)
    elif isinstance(value, list):
        for item in value:
            yield from contents(item)


class Tests(unittest.TestCase):
    def test_model_table_to_native_fields(self):
        built = card_view.answer_card('### **对照**\n\n| 计划 | 消耗 | 预计利润 |\n|---|---:|---:|\n| 托管 | 4,562.86 | -52.06 |\n\n**提醒**\n- 仅为预测[1]')
        text = '\n'.join(contents(built))
        self.assertIn('消耗\n4,562.86', text)
        self.assertIn('预计利润\n-52.06', text)
        self.assertIn('托管', text)
        self.assertIn('仅为预测[1]', text)
        for token in ('**', '###', '|---', 'lark_md'):
            self.assertNotIn(token, json.dumps(built, ensure_ascii=False))
        self.assertTrue(any('fields' in element for element in built['elements']))

    def test_numeric_fields_are_from_report(self):
        built = card_view.report_reply(sample(), analysis='### 说明\n**仅为模型预测**')
        text = '\n'.join(contents(built))
        for expected in ('61,489.32', '2,335.07', '-52.06', '9000000000000006', '2026-10-20', '—'):
            self.assertIn(expected, text)
        self.assertNotIn('**', text)
        for outgoing in cards.build(sample()):
            self.assertNotIn('lark_md', json.dumps(outgoing))
            self.assertNotIn('**', json.dumps(outgoing))

    def test_report_route_reply_and_followup_keep_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'model_test.json'
            path.write_text(json.dumps(sample()), encoding='utf-8')
            memory = Conversation(root / 'memory.db')
            nf = Mock()
            context = {'chat_id': 'c', 'sender_id': 'u'}
            listener.reply_command(nf, 'om_1', '报告', outdir=root, context=context, memory=memory)
            nf.reply_card.assert_called_once()
            nf.reply_text.assert_not_called()
            nf.reply_markdown.assert_not_called()
            self.assertIn('61,489.32', json.dumps(nf.reply_card.call_args.args[1], ensure_ascii=False))
            changed = sample()
            changed['audit']['end'] = '2026-10-25'
            changed['totals']['gmv'] = '999999.99'
            path.write_text(json.dumps(changed), encoding='utf-8')
            nf.reset_mock()
            with patch.object(listener.Knowledge, 'ask', return_value='简短结论'):
                # Mock successful API capture, while retaining original report snapshot.
                def success(self, *args, **kwargs):
                    self.last_context = kwargs['report_context']
                    return '简短结论'
                with patch.object(listener.Knowledge, 'ask', success):
                    listener.reply_command(nf, 'om_2', '用表格显示', outdir=root,
                                           env={'KNOWLEDGE_ENABLED': 'true'}, context=context, memory=memory)
            result = json.dumps(nf.reply_card.call_args.args[1], ensure_ascii=False)
            self.assertIn('2026-10-20', result)
            self.assertIn('61,489.32', result)
            self.assertNotIn('999,999.99', result)

    def test_data_summary_and_reformat_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, 'model_test.json').write_text(json.dumps(sample()), encoding='utf-8')
            memory = Conversation(root/'memory.db')
            nf = Mock()
            ctx = {'chat_id':'c','sender_id':'u'}
            env = {'KNOWLEDGE_ENABLED':'true'}
            with patch.object(listener.Knowledge, 'ask', side_effect=AssertionError('数据表格不能调用LLM')):
                listener.reply_command(nf, '1', '表格', outdir=root, env=env, context=ctx, memory=memory)
            data_card = nf.reply_card.call_args.args[1]
            self.assertTrue(any('fields' in e for e in data_card['elements']))
            self.assertIn('逐计划对照', json.dumps(data_card,ensure_ascii=False))
            nf.reset_mock()
            def summarize(self, question, env, report_context=None):
                self.last_context = report_context
                self_test.assertIn('约150字', question)
                return '1. 整体预计盈利。\n2. 托管贡献主要成交。\n3. 注意退款假设。'
            self_test = self
            with patch.object(listener.Knowledge, 'ask', summarize):
                listener.reply_command(nf, '2', '总结日报', outdir=root, env=env, context=ctx, memory=memory)
            summary_card = nf.reply_card.call_args.args[1]
            self.assertFalse(any('fields' in e for e in summary_card['elements']))
            self.assertNotIn('逐计划对照', json.dumps(summary_card,ensure_ascii=False))
            self.assertIn('整体预计盈利', json.dumps(summary_card,ensure_ascii=False))
            nf.reset_mock()
            with patch.object(listener.Knowledge, 'ask', side_effect=AssertionError('转换格式不能调用LLM')):
                listener.reply_command(nf, '3', '表格显示', outdir=root, env=env, context=ctx, memory=memory)
            converted = nf.reply_card.call_args.args[1]
            self.assertTrue(any('fields' in e for e in converted['elements']))
            self.assertIn('整体预计盈利', json.dumps(converted,ensure_ascii=False))
            self.assertNotIn('逐计划对照', json.dumps(converted,ensure_ascii=False))
            self.assertNotIn('61,489.32', json.dumps(converted,ensure_ascii=False))
            nf.reset_mock()
            listener.reply_command(nf, '4', '表格显示', outdir=root, env=env,
                                   context={'chat_id':'c','sender_id':'new'}, memory=memory)
            self.assertIn('没有可转换',nf.reply_markdown.call_args.args[1])

    def test_quote_parser_keeps_native_fields(self):
        item = {'body': {'content': json.dumps(card_view.report_reply(sample()))}}
        self.assertIn('61,489.32', listener.message_text(item))
        self.assertIn('预计利润', listener.message_text(item))

    def test_original_message_endpoint(self):
        nf = notify.FeishuAppNotifier('app', 'secret', 'oc_unused')
        nf._tenant_token = lambda: 'test'
        nf._request = Mock(return_value={'code': 0})
        outgoing = card_view.report_reply(sample())
        nf.reply_card('om_test', outgoing)
        args = nf._request.call_args.args
        self.assertTrue(args[1].endswith('/om_test/reply'))
        self.assertEqual(json.loads(args[2]['content']), outgoing)
        self.assertNotIn('receive_id', args[2])


if __name__ == '__main__':
    unittest.main()
