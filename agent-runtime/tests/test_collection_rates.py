"""Read-only SQLite evidence and deterministic interval/balance computations."""
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import monitor as m, hourly as h, listener

NOW = datetime(2026, 10, 30, 10, 31, tzinfo=h.CHINA)
ENV = {'QC_ADVERTISER_IDS': '999', 'FEISHU_CHAT_ID': 'oc_test'}


def snapshot(at, spend, kind='plan', account='999', empty=False):
    return {'account': account, 'kind': kind, 'day': at.date().isoformat(), 'bucket': m.bucket(at).isoformat(),
            'fetched_at': (at+timedelta(seconds=2)).isoformat(), 'request_end': at.isoformat(),
            'topic': m.TOPICS[kind][0], 'scope': m.gate.SCOPES[kind], 'unit': 'CNY', 'rows': [] if empty else
            [{'id': '123', 'name': '测试计划', 'ad_spend': spend, 'gmv': '0', 'order_count': '0',
              'backend_net': '0', 'comprehensive_cost': spend}]}


def balance(at, value='132.56', account='999'):
    return {'advertiser_id': account, 'fetched_at': at.isoformat(), 'amounts_yuan':
            {'account_total': value, 'account_valid': value, 'account_frozen': '0'}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agent.collection_rates'), 'Read-only query is missing')
        self.query = importlib.import_module('agent.collection_rates')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = m.Store(self.root)
        self.net = patch('urllib.request.urlopen', side_effect=AssertionError('No real network'))
        self.net.start(); self.addCleanup(self.net.stop)

    def seed(self):
        for at, spend in [(NOW.replace(hour=9,minute=31,second=9), '100'),
                          (NOW.replace(hour=10,minute=1,second=9), '110'),
                          (NOW.replace(hour=10,minute=30,second=15), '124.95')]:
            self.store.save_snapshot(snapshot(at, spend))
            self.store.save_snapshot(snapshot(at, '0', kind='material', empty=True))
        self.store.save_balance(balance(NOW.replace(minute=30,second=26)))

    def test_exact_elapsed_rate_and_balance_speed_remain_separate(self):
        self.seed(); status = self.query.inspect(self.root, ENV, NOW)
        plan = status['plan']
        self.assertEqual(plan['point_state'], 'collected')
        self.assertEqual(plan['assessment']['state'], 'ready')
        self.assertEqual(plan['rows'][0]['spend_delta'], '14.95')
        self.assertEqual(Decimal(plan['rows'][0]['minutes']), Decimal('29.1'))
        self.assertEqual(format(Decimal(plan['spend_rate']), '.2f'), '30.82')
        self.assertIsNone(status['balance']['rate']['rate'])
        self.assertIn('基准', status['balance']['rate']['reason'])
        text = str(self.query.rendered(status))
        self.assertIn('132.56', text); self.assertIn('30.82', text)
        self.assertIn('无法计算', text)

    def test_query_does_not_create_database_or_schema(self):
        self.assertFalse(self.store.path.exists())
        status = self.query.inspect(self.root, ENV, NOW)
        self.assertFalse(self.store.path.exists())
        self.assertIsNone(status['plan']['spend_rate'])
        self.seed()
        before = self.store.path.read_bytes()
        with closing(sqlite3.connect(self.store.path)) as db:
            schema = db.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()
        self.query.inspect(self.root, ENV, NOW)
        self.assertEqual(before, self.store.path.read_bytes())
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(schema, db.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall())

    def test_empty_material_is_saved_empty_not_zero_speed(self):
        self.seed(); item = self.query.inspect(self.root, ENV, NOW)['material']
        self.assertEqual(item['point_state'], 'collected')
        self.assertEqual(item['record_count'], 0)
        self.assertIsNone(item['spend_rate'])
        self.assertIn('空', str(self.query.rendered(self.query.inspect(self.root, ENV, NOW))))

    def test_unknown_account_does_not_read_other_account(self):
        self.seed(); status = self.query.inspect(self.root, dict(ENV, QC_ADVERTISER_IDS='888'), NOW)
        self.assertIsNone(status['plan']['spend_rate'])
        self.assertIsNone(status['balance']['sample'])
        self.assertNotIn('132.56', str(self.query.rendered(status)))

    def test_saved_zero_spend_is_valid_zero(self):
        for i in (2,1,0): self.store.save_snapshot(snapshot(NOW.replace(minute=30)-timedelta(minutes=30*i), '0'))
        item = self.query.inspect(self.root, ENV, NOW)['plan']
        self.assertEqual(Decimal(item['spend_rate']), 0)

    def test_current_bucket_is_waiting_then_overdue_even_with_fresh_old_snapshot(self):
        self.seed()
        with self.store.db() as db: db.execute("DELETE FROM snapshots WHERE bucket LIKE '%T10:30:%'")
        early = self.query.inspect(self.root, ENV, NOW)['plan']
        late = self.query.inspect(self.root, ENV, NOW.replace(minute=36))['plan']
        self.assertEqual(early['point_state'], 'waiting')
        self.assertEqual(late['point_state'], 'overdue')

    def test_stale_or_revised_data_never_produces_current_speed(self):
        self.seed(); status = self.query.inspect(self.root, ENV, NOW+timedelta(hours=2))
        self.assertIsNone(status['plan']['spend_rate']); self.assertIsNone(status['balance']['rate']['rate'])
        self.store.save_snapshot(snapshot(NOW.replace(minute=30,second=15), '90'))
        self.assertIsNone(self.query.inspect(self.root, ENV, NOW)['plan']['spend_rate'])

    def test_corrupt_record_is_unavailable_not_zero(self):
        self.seed()
        with self.store.db() as db: db.execute("UPDATE snapshots SET payload='broken' WHERE kind='plan'")
        status = self.query.inspect(self.root, ENV, NOW)
        self.assertIsNone(status['plan']['spend_rate']); self.assertTrue(status['plan']['error'])

    def test_four_hour_balance_window_crosses_midnight(self):
        end = NOW.replace(hour=0,minute=30)
        for i in range(9): self.store.save_balance(balance(end-timedelta(minutes=30*(8-i)), str(2000+50*(8-i))))
        status = self.query.inspect(self.root, ENV, end+timedelta(minutes=1))
        self.assertEqual(Decimal(status['balance']['rate']['rate']), 100)
        self.assertIn('2026-10-29', status['balance']['rate']['start'])

    def test_invalid_spend_renders_unavailable_without_crashing_or_zero(self):
        for bad in ('broken', 'NaN', 'Infinity', '-1', None):
            with self.subTest(value=bad):
                self.seed()
                value = snapshot(NOW.replace(minute=30, second=15), bad)
                if bad is None: value['rows'][0].pop('ad_spend')
                self.store.save_snapshot(value)
                status = self.query.inspect(self.root, ENV, NOW)
                self.assertIsNone(status['plan']['spend_rate'])
                row = status['plan']['rows'][0]
                self.assertIsNone(row['after_spend'])
                text = str(self.query.rendered(status))
                self.assertIn('累计110.00→不可用', text)
                self.assertIn('速率不可用', text)
                self.assertNotIn('NaN', text)
                self.assertNotIn('Infinity', text)

    def test_direct_query_reply_bypasses_llm_and_requires_allowlisted_group(self):
        self.seed(); nf = Mock()
        with patch.object(self.query.h, 'now_china', return_value=NOW), \
             patch('agent.knowledge.Knowledge.ask', side_effect=AssertionError('No LLM')):
            listener.reply_command(nf, 'om-test', '采集与速率', self.root/'out', ENV, {'chat_id':'oc_test'})
        nf.reply_card.assert_called_once()
        self.assertIn('30.82', str(nf.reply_card.call_args))
        nf.reset_mock()
        listener.reply_command(nf, 'om-test', '采集与速率', self.root/'out', ENV, {'chat_id':'oc_other'})
        nf.reply_card.assert_not_called()


if __name__ == '__main__': unittest.main()
