"""Real SQLite threshold state with mocked transport; no live API or group sends."""
import copy
from datetime import datetime, timedelta
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import monitor as m, hourly as h, settings

NOW = datetime(2026, 10, 30, 10, 30, tzinfo=h.CHINA)
ENV = {'QC_ADVERTISER_IDS': '999', 'FEISHU_CHAT_ID': 'oc_test', 'QC_ACCESS_TOKEN': 'fake'}


def sample(at, value='132.56', account='999'):
    return {'advertiser_id': account, 'fetched_at': at.isoformat(), 'amounts_yuan':
            {'account_total': value, 'account_valid': value, 'account_frozen': '0'}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'config').mkdir()
        self.cfg = settings.load_config(); self.cfg['monitoring']['enabled'] = True
        (self.root/'config/rules.json').write_text(json.dumps(self.cfg), encoding='utf-8')
        self.store = m.Store(self.root); self.nf = Mock()
        self.net = patch('urllib.request.urlopen', side_effect=AssertionError('No real network'))
        self.net.start(); self.addCleanup(self.net.stop)

    def tick(self, at=NOW, amount='132.56', env=None):
        def fetch(env, kind, now):
            return {'account': '999', 'kind': kind, 'day': now.date().isoformat(),
                    'bucket': m.bucket(now).isoformat(), 'fetched_at': now.isoformat(),
                    'request_end': now.isoformat(), 'topic': m.TOPICS[kind][0],
                    'scope': m.gate.SCOPES[kind], 'unit': 'CNY', 'rows': []}
        with patch.object(m, 'fetch_snapshot', side_effect=fetch), \
             patch.object(h, 'now_china', return_value=at), \
             patch.object(m.account_balance, 'fetch', return_value=sample(at, amount)):
            m.Engine(self.root, env or ENV, lambda chat: self.nf).tick(at)

    def watch(self):
        self.assertIsNotNone(importlib.util.find_spec('agent.balance_watch'), 'Threshold watch is missing')
        return importlib.import_module('agent.balance_watch')

    def test_low_balance_sends_outside_four_hour_window(self):
        self.tick()
        self.assertEqual(self.nf.send_card.call_count, 1)
        text = str(self.nf.send_card.call_args.args[0])
        self.assertIn('132.56', text)
        self.assertIn('500', text)

    def test_repeated_low_and_restart_do_not_send_again(self):
        self.tick(); self.tick(NOW+timedelta(minutes=1))
        self.store.save_balance(sample(NOW+timedelta(minutes=30), '100'))
        self.tick(NOW+timedelta(minutes=30), '100')
        self.assertEqual(self.nf.send_card.call_count, 1)

    def test_recovery_is_silent_and_later_crossing_rearms(self):
        self.tick()
        self.store.save_balance(sample(NOW+timedelta(minutes=1), '900'))
        self.tick(NOW+timedelta(minutes=1), '900')
        self.assertEqual(self.nf.send_card.call_count, 1)
        self.store.save_balance(sample(NOW+timedelta(minutes=2), '500'))
        self.tick(NOW+timedelta(minutes=2), '500')
        self.assertEqual(self.nf.send_card.call_count, 2)

    def test_failed_send_retries_same_uuid(self):
        self.nf.send_card.side_effect = [TimeoutError(), None]
        with self.assertRaises(TimeoutError): self.tick()
        first = self.nf.send_card.call_args.kwargs['request_uuid']
        self.tick(NOW+timedelta(minutes=1))
        self.assertEqual(self.nf.send_card.call_count, 2)
        self.assertEqual(first, self.nf.send_card.call_args.kwargs['request_uuid'])

    def test_expired_undelivered_notice_replaced_using_new_sample(self):
        self.nf.send_card.side_effect = [TimeoutError(), None]
        with self.assertRaises(TimeoutError): self.tick()
        first = self.nf.send_card.call_args.kwargs['request_uuid']
        self.tick(NOW+timedelta(hours=1), '100')
        self.assertEqual(self.nf.send_card.call_count, 2)
        self.assertNotEqual(first, self.nf.send_card.call_args.kwargs['request_uuid'])
        self.assertEqual(self.store.pending('999'), [])

    def test_recovery_cancels_unsent_low_notice(self):
        self.nf.send_card.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError): self.tick()
        self.store.save_balance(sample(NOW+timedelta(minutes=1), '900'))
        self.nf.send_card.side_effect = None
        self.tick(NOW+timedelta(minutes=1), '900')
        self.assertEqual(self.nf.send_card.call_count, 1)
        self.assertEqual(self.store.pending('999'), [])

    def test_four_hour_summary_covers_threshold_without_duplicate(self):
        self.tick(NOW.replace(hour=12, minute=0))
        self.assertEqual(self.nf.send_card.call_count, 1)
        self.assertIn('132.56', str(self.nf.send_card.call_args.args[0]))
        self.tick(NOW.replace(hour=12, minute=1))
        self.assertEqual(self.nf.send_card.call_count, 1)

    def test_recovery_retires_unsent_coalesced_low_summary(self):
        at = NOW.replace(hour=12, minute=0)
        self.nf.send_card.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError): self.tick(at)
        self.assertEqual(len(self.store.pending('999')), 1)
        self.store.save_balance(sample(at+timedelta(minutes=1), '900'))
        self.nf.send_card.side_effect = None
        self.tick(at+timedelta(minutes=1), '900')
        self.assertEqual(self.nf.send_card.call_count, 1, 'Recovered balance must not send old low summary')
        self.assertEqual(self.store.pending('999'), [])
        self.tick(at+timedelta(hours=4), '900')
        self.assertEqual(self.nf.send_card.call_count, 2, 'Future scheduled summaries remain enabled')
        self.assertIn('900.00', str(self.nf.send_card.call_args))

    def test_fresh_sample_hook_checks_every_read_and_coalesces_same_tick(self):
        at = NOW.replace(hour=12, minute=0)
        from agent import balance_watch
        with patch.object(balance_watch, 'observe', wraps=balance_watch.observe) as observed:
            self.tick(at)
        self.assertGreaterEqual(observed.call_count, 2)
        self.assertEqual(self.nf.send_card.call_count, 1)

    def test_invalid_target_does_not_send(self):
        with self.assertRaises(ValueError): self.tick(env=dict(ENV, FEISHU_ALERT_CHAT_ID='oc_other'))
        self.nf.send_card.assert_not_called()

    def test_stale_or_out_of_order_samples_do_not_reset_watch(self):
        watch = self.watch()
        self.assertIsNone(watch.observe(self.store, sample(NOW-timedelta(hours=1)), self.cfg, now=NOW))
        watch.observe(self.store, sample(NOW, '900'), self.cfg, now=NOW)
        watch.observe(self.store, sample(NOW-timedelta(minutes=1)), self.cfg, now=NOW)
        self.assertEqual(self.store.pending('999'), [])

    def test_invalid_amount_does_not_queue_or_change_schema(self):
        watch = self.watch()
        self.store.save_balance(sample(NOW, '900'))
        with self.store.db() as db:
            before = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        with self.assertRaises(ValueError): watch.observe(self.store, sample(NOW, 'NaN'), self.cfg, now=NOW)
        with self.store.db() as db:
            self.assertEqual(before, db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall())
        self.assertEqual(self.store.pending('999'), [])

    def test_queued_threshold_is_retired_by_legacy_balance_consumer_on_rollback(self):
        watch = self.watch()
        watch.observe(self.store, sample(NOW), self.cfg, now=NOW)
        pending = self.store.pending('999')
        self.assertEqual(len(pending), 1)
        # V1 retires every pending balance: event. Keep this queue protocol rollback-safe.
        for key, payload, stamp in pending:
            if key.startswith('balance:'): self.store.mark_sent(key, state=2)
        self.assertEqual(self.store.pending('999'), [], 'V1 must not send V2 threshold notices after rollback')


if __name__ == '__main__': unittest.main()
