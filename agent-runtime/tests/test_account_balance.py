"""Live balance conversion, isolation, errors and native command routing."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import account_balance as balance, listener
from agent.conversation import Conversation

ENV = {'QC_ADVERTISER_IDS': '999', 'QC_ACCESS_TOKEN': 'secret-token',
       'FEISHU_ALLOWED_CHAT_IDS': 'test-chat'}
PAYLOAD = {'code': 0, 'data': {'advertiser_id': 999,
           'account_total': 224798000, 'account_valid': 224798000, 'account_frozen': 0}}


class Tests(unittest.TestCase):
    def test_amount_unit_and_zero(self):
        snapshot = balance.fetch(ENV, request=lambda *args: PAYLOAD)
        self.assertEqual(snapshot['amounts_yuan']['account_valid'], '2247.98')
        self.assertEqual(snapshot['amounts_yuan']['account_frozen'], '0')
        rendered, description = balance.describe(snapshot)
        self.assertIn('2,247.98 元', description)
        self.assertIn('北京时间', description)
        self.assertNotIn('**', json.dumps(rendered))
        tiny = copy.deepcopy(PAYLOAD)
        tiny['data']['account_valid'] = 1
        self.assertEqual(balance.fetch(ENV, request=lambda *a: tiny)['amounts_yuan']['account_valid'], '0.00001')

    def test_bad_responses_never_become_zero_or_leak(self):
        bads = [None, {}, {'code': 403, 'message': 'secret-token'}]
        for field, value in [('advertiser_id', 888), ('account_valid', None),
                             ('account_valid', 'NaN'), ('account_valid', 'Infinity'),
                             ('account_valid', 1.5)]:
            p = copy.deepcopy(PAYLOAD)
            p['data'][field] = value
            bads.append(p)
        missing = copy.deepcopy(PAYLOAD)
        del missing['data']['account_valid']
        bads.append(missing)
        for bad in bads:
            with self.assertRaises(ValueError) as error:
                balance.fetch(ENV, request=lambda *a: bad)
            self.assertNotIn('secret-token', str(error.exception))
        with self.assertRaises(ValueError):
            balance.fetch(ENV, request=Mock(side_effect=TimeoutError('secret-token')))

    def test_configuration_no_request(self):
        for updates in ({'QC_ADVERTISER_IDS': ''}, {'QC_ADVERTISER_IDS': '999,888'},
                        {'QC_ACCESS_TOKEN': ''}):
            request = Mock()
            with self.assertRaises(ValueError):
                balance.fetch(dict(ENV, **updates), request=request)
            request.assert_not_called()

    def test_intents(self):
        for text in ('余额', '查询余额', '当前余额', '账户余额是多少？', '查看千川账号余额',
                     '请帮我查询当前千川账号的余额为多少？'):
            self.assertTrue(balance.matches(text), text)
        for text in ('余额不足怎么办', '如何充值余额', '余额为什么减少', '预算', '报告', '生成10.22日报'):
            self.assertFalse(balance.matches(text), text)

    def test_listener_no_report_no_llm_native_reply_and_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = Conversation(root / 'memory.db')
            nf = Mock()
            context = {'chat_id': 'test-chat', 'sender_id': 'user', 'parent_id': 'quoted'}
            with patch.object(listener, 'BASE', root), \
                 patch.object(balance, '_get', return_value=PAYLOAD) as get, \
                 patch.object(listener, '_latest_report', side_effect=AssertionError('no report')), \
                 patch.object(listener, 'Knowledge', side_effect=AssertionError('no LLM')):
                listener.reply_command(nf, 'm1', '查询余额', env=ENV, context=context, memory=memory)
                get.assert_called_once_with('999', 'secret-token')
                listener.reply_command(nf, 'm2', '当前余额', env=ENV, context=context, memory=memory)
                self.assertEqual(get.call_count, 2)  # fresh query each time
            self.assertEqual(nf.reply_card.call_count, 2)
            self.assertEqual(len(nf.method_calls), 2)
            self.assertEqual(nf.reply_card.call_args.args[0], 'm2')
            self.assertEqual(len(list((root / 'data/qianchuan').glob('account_balance_*.json'))), 2)
            saved = memory.load('test-chat', 'user')[-1]
            self.assertIn('2247.98', saved['source'])
            self.assertNotIn('secret-token', saved['source'])
            self.assertIsNone(saved['report_snapshot'])

    def test_allowlist_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            nf = Mock()
            with patch.object(balance, '_get', side_effect=TimeoutError('secret-token')) as get:
                balance.reply(nf, 'm', '余额', tmp, ENV, {'chat_id': 'other'})
                get.assert_not_called()
                nf.reply_card.assert_not_called()
                balance.reply(nf, 'm', '余额', tmp, ENV, {'chat_id': 'test-chat'})
            text = json.dumps(nf.reply_card.call_args.args[1], ensure_ascii=False)
            self.assertIn('查询失败', text)
            self.assertNotIn('0.00', text)
            self.assertNotIn('secret-token', text)
            self.assertFalse((Path(tmp) / 'data').exists())


if __name__ == '__main__':
    unittest.main()
