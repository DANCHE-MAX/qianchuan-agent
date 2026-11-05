"""Budget modes, account scope, failure isolation, snapshot provenance and native replies."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import budgets, listener, card_view
from agent.conversation import Conversation

STAMP = '2026-10-26T16:00:00.000000+08:00'


def payload(mode='BUDGET_MODE_DAY', amount=5000, pid='123'):
    return {'code': 0, 'data': {'ad_id': pid,
            'delivery_setting': {'budget_mode': mode, 'budget': amount}}}


def report():
    return {'audit': {'start': '2026-10-25', 'end': '2026-10-25', 'days': 1},
            'source_api': {'advertiser_id': '999'},
            'totals': {'ad_spend': '25.30', 'predicted_profit': '7.00'},
            'plans': [{'plan_id': '123', 'plan_name': '马甲', 'ad_spend': '25.30'}]}


class Tests(unittest.TestCase):
    env = {'QC_ADVERTISER_IDS': '999', 'QC_ACCESS_TOKEN': 'do-not-display'}

    def test_day_total_infinite_and_missing(self):
        for mode in ('BUDGET_MODE_DAY', 'BUDGET_MODE_TOTAL'):
            b = budgets.parse(payload(mode), '123', STAMP)
            self.assertEqual(b['amount'], '5000')
            self.assertEqual(b['mode'], mode)
            self.assertIn('5,000.00', budgets.describe({'budget_snapshot': b}))
        b = budgets.parse(payload('BUDGET_MODE_INFINITE', 0), '123', STAMP)
        self.assertEqual(b['status'], 'ok')
        self.assertIsNone(b['amount'])
        text = budgets.describe({'budget_snapshot': b})
        self.assertIn('不限预算', text)
        self.assertNotIn('0.00 元', text)
        for bad in (None, -1, 'NaN', 'Infinity'):
            self.assertEqual(budgets.parse(payload(amount=bad), '123', STAMP)['status'], 'unavailable')
        for data in ({'code': 50000}, payload('new-mode'), payload(pid='124')):
            self.assertEqual(budgets.parse(data, '123', STAMP)['status'], 'unavailable')
        self.assertEqual(budgets.parse(payload(amount=0), '123', STAMP)['amount'], '0')

    def test_account_scope_and_partial_failure(self):
        request = Mock()
        with self.assertRaises(ValueError):
            budgets.fetch(self.env, '888', report()['plans'], request=request)
        request.assert_not_called()
        def get(a, pid, token):
            if pid == '124':
                raise TimeoutError('sensitive-server-detail')
            return payload(pid=pid)
        s = budgets.fetch(self.env, '999', [{'plan_id':'123'}, {'plan_id':'124'}], request=get)
        self.assertEqual([p['status'] for p in s['plans']], ['ok', 'unavailable'])
        text = json.dumps(s)
        self.assertNotIn('sensitive-server-detail', text)
        self.assertNotIn('do-not-display', text)

    def test_snapshot_merge_new_failure_not_stale_success_and_financials(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = {'advertiser_id': '999', 'kind': 'current_budget', 'fetched_at': STAMP,
                     'scope_note': '当前设置，非历史预算', 'plans': [budgets.parse(payload(), '123', STAMP),
                         budgets.parse(payload(pid='124'), '124', STAMP)]}
            budgets.save(first, tmp)
            later = STAMP.replace('16:00', '17:00')
            second = dict(first, fetched_at=later, plans=[budgets.parse({'code': 50000}, '123', later)])
            budgets.save(second, tmp)
            merged = budgets.latest(tmp, '999')
            self.assertEqual(len(list(Path(tmp).glob('*.json'))), 2)
            self.assertEqual(len(merged['plans']), 2)
            r = report()
            before = copy.deepcopy(r)
            budgets.attach(r, merged)
            self.assertEqual(r['totals'], before['totals'])
            self.assertEqual(r['plans'][0]['ad_spend'], '25.30')
            self.assertIsNone(r['plans'][0]['configured_budget'])
            self.assertIn('未获取', budgets.describe(r['plans'][0]))
            self.assertIsNone(budgets.latest(tmp, '888'))
            unchanged = report()
            budgets.attach(unchanged, dict(first, advertiser_id='888'))
            self.assertEqual(unchanged, before)

    def test_live_query_one_native_reply_no_llm_and_followup_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, 'model_test.json').write_text(json.dumps(report()), encoding='utf-8')
            memory = Conversation(root/'memory.db')
            nf = Mock()
            ctx = {'chat_id':'test-chat', 'sender_id':'test-user'}
            with patch.object(listener, 'BASE', root), \
                 patch.object(budgets, '_get', return_value=payload()) as request, \
                 patch.object(listener.Knowledge, 'ask', side_effect=AssertionError('no LLM')):
                listener.reply_command(nf, 'm1', '预算 马甲', outdir=root, env=self.env, context=ctx, memory=memory)
                request.assert_called_once_with('999', '123', 'do-not-display')
            self.assertEqual(len(nf.method_calls), 1)
            text = json.dumps(nf.reply_card.call_args.args[1], ensure_ascii=False)
            for value in ('5,000.00', '日预算', '读取时间', '非历史预算', '2026-10-25'):
                self.assertIn(value, text)
            self.assertNotIn('**', text)
            self.assertNotIn('do-not-display', text)
            self.assertIn('预算快照', memory.load('test-chat','test-user')[-1]['source'])
            nf.reset_mock()
            with patch.object(budgets, '_get', side_effect=AssertionError('unknown plan')):
                listener.reply_command(nf, 'm2', '预算 不存在', outdir=root, env=self.env, memory=memory)
            self.assertIn('未找到', nf.reply_text.call_args.args[1])

    def test_intents_do_not_hijack_advice_or_other_commands(self):
        self.assertEqual(listener.budget_term('查看所有计划预算'), '')
        self.assertEqual(listener.budget_term('马甲计划预算是多少？'), '马甲')
        for question in ('如何调整预算', '预算为什么花不出去', '报告', '计划 马甲'):
            self.assertIsNone(listener.budget_term(question))


if __name__ == '__main__':
    unittest.main()
