"""Fault injection only: no live credentials, real API calls or group messages."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import qc_auth, settings, delivery, listener, notify, runtime, reliability


def env():
    return {'QC_APP_ID': '123', 'QC_APP_SECRET': 'fake-secret',
            'QC_ACCESS_TOKEN': 'old-access', 'QC_REFRESH_TOKEN': 'old-refresh'}


def refreshed():
    return {'code': 0, 'data': {'access_token': 'new-access', 'refresh_token': 'new-refresh',
                              'expires_in': 86400, 'refresh_token_expires_in': 2592000}}


class TokenTests(unittest.TestCase):
    def test_refresh_expiry_and_rotation(self):
        e = dict(env(), QC_ACCESS_TOKEN_EXPIRES_AT='1200')
        request = Mock(return_value=refreshed())
        manager = qc_auth.TokenManager(e, clock=lambda: 1000, request=request)
        self.assertEqual(manager.token(), 'new-access')
        self.assertEqual(e['QC_REFRESH_TOKEN'], 'new-refresh')
        self.assertEqual(float(e['QC_ACCESS_TOKEN_EXPIRES_AT']), 87400)
        self.assertEqual(manager.token(), 'new-access')
        request.assert_called_once()

    def test_no_unknown_expiry_guess(self):
        request = Mock()
        m = qc_auth.TokenManager(env(), request=request)
        self.assertEqual(m.token(), 'old-access')
        request.assert_not_called()

    def test_expired_response_refresh_exactly_once_and_permission_no_refresh(self):
        for after in ({'code': 0}, {'code': 40102}):
            e = env()
            operation = Mock(side_effect=[{'code': 40102}, after])
            with patch.object(qc_auth, '_refresh_request', return_value=refreshed()) as refresh:
                if after['code']:
                    with self.assertRaises(reliability.ServiceError):
                        qc_auth.call(e, operation)
                else:
                    self.assertEqual(qc_auth.call(e, operation), after)
                refresh.assert_called_once()
            self.assertEqual([c.args[0] for c in operation.call_args_list], ['old-access', 'new-access'])
        for code in (40002, 40104, 40118, 40000):
            with patch.object(qc_auth, '_refresh_request') as refresh:
                with self.assertRaises(reliability.ServiceError):
                    qc_auth.call(env(), lambda token: {'code': code})
                refresh.assert_not_called()

    def test_refresh_failure_is_sanitized_and_cooled_down(self):
        e = env()
        request = Mock(return_value={'code': 40107, 'message': 'fake-secret old-refresh'})
        m = qc_auth.TokenManager(e, clock=lambda: 1000, request=request)
        for _ in range(2):
            with self.assertRaisesRegex(reliability.ServiceError, '重新授权') as error:
                m.token(rejected='old-access')
            self.assertNotIn('fake-secret', str(error.exception))
        request.assert_called_once()
        self.assertEqual(e['QC_ACCESS_TOKEN'], 'old-access')

    def test_refresh_transport_uncertainty_no_blind_retry(self):
        request = Mock(side_effect=TimeoutError('fake-secret'))
        m = qc_auth.TokenManager(env(), clock=lambda: 1000, request=request)
        for _ in range(2):
            with self.assertRaises(reliability.ServiceError) as error:
                m.token(rejected='old-access')
            self.assertTrue(error.exception.transient)
            self.assertNotIn('fake-secret', str(error.exception))
        request.assert_called_once()

    def test_file_rotation_preserves_other_config_and_concurrent_callers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('# comment\nOTHER=value\n' + '\n'.join(k+'='+v for k,v in env().items()), encoding='utf-8')
            stale = settings.load_env(path)
            request = Mock(return_value=refreshed())
            managers = [qc_auth.TokenManager(settings.load_env(path), request=request) for _ in range(4)]
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda m: m.token(rejected='old-access'), managers))
            self.assertEqual(results, ['new-access'] * 4)
            request.assert_called_once()
            self.assertIn('# comment\nOTHER=value', path.read_text(encoding='utf-8'))
            self.assertEqual(qc_auth.TokenManager(stale).token(), 'new-access')
            self.assertFalse((path.parent / '.qc-token.pending').exists())

    def test_persist_failure_recovers_without_second_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('\n'.join(k+'='+v for k,v in env().items()), encoding='utf-8')
            request = Mock(return_value=refreshed())
            m = qc_auth.TokenManager(settings.load_env(path), request=request)
            with patch.object(m, '_commit', side_effect=PermissionError('private details')):
                with self.assertRaises(reliability.ServiceError):
                    m.token(force=True)
            self.assertTrue((path.parent / '.qc-token.pending').exists())
            self.assertEqual(m.token(), 'new-access')
            request.assert_called_once()

    def test_api_transient_retry_bounded_and_business_no_retry(self):
        operation = Mock(side_effect=[{'code': 40110}, TimeoutError(), {'code': 0}])
        with patch.object(qc_auth, 'retry_io', side_effect=lambda fn: reliability.retry_io(fn, sleep=lambda t: None)):
            self.assertEqual(qc_auth.call(env(), operation), {'code': 0})
        self.assertEqual(operation.call_count, 3)
        operation = Mock(return_value={'code': 40002})
        with self.assertRaises(reliability.ServiceError):
            qc_auth.call(env(), operation)
        operation.assert_called_once()


class DeliveryTests(unittest.TestCase):
    def test_saved_answer_replayed_after_restart_without_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'outbox.db'
            box = delivery.Outbox(path)
            handler = Mock(side_effect=lambda nf, mid, cmd, **kw: nf.reply_text(mid, 'fixed answer'))
            nf = Mock()
            nf.reply_text.side_effect = TimeoutError()
            with self.assertRaises(TimeoutError):
                delivery.dispatch(nf, None, 'msg', 'question', handler, box)
            first = nf.reply_text.call_args
            nf.reply_text.side_effect = None
            delivery.dispatch(nf, None, 'msg', 'question', handler, delivery.Outbox(path))
            handler.assert_called_once()
            self.assertEqual(first, nf.reply_text.call_args)
            delivery.dispatch(nf, None, 'msg', 'question', handler, delivery.Outbox(path))
            self.assertEqual(nf.reply_text.call_count, 2)

    def test_partial_cards_resume_and_expired_uncertain_delivery_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = delivery.Outbox(Path(tmp) / 'outbox.db')
            box.save('msg', [{'message_id':'msg','kind':'card','content':{'page':i}} for i in range(2)])
            nf = Mock()
            nf.reply_card.side_effect = [{}, TimeoutError()]
            with self.assertRaises(TimeoutError):
                box.deliver(nf, 'msg', clock=lambda: 100)
            self.assertEqual(box.load('msg')[1], 1)
            nf.reset_mock()
            nf.reply_card.side_effect = None
            with self.assertRaisesRegex(reliability.DeliveryError, '安全窗口'):
                box.deliver(nf, 'msg', clock=lambda: 4000)
            nf.reply_card.assert_not_called()
            box.deliver(nf, 'msg', clock=lambda: 200)
            self.assertEqual(nf.reply_card.call_args.args[1], {'page': 1})

    def test_http_failure_classification_and_reply_uuid(self):
        nf = notify.FeishuAppNotifier('app', 'private-secret', 'chat')
        for status, code, transient in ((403, 40002, False), (429, 99991400, True), (503, 0, True), (400, 99991400, True)):
            error = urllib.error.HTTPError('https://example.invalid', status, 'private-secret', {},
                                           io.BytesIO(json.dumps({'code':code, 'msg':'private-secret'}).encode()))
            with patch('urllib.request.urlopen', side_effect=error):
                with self.assertRaises(reliability.DeliveryError) as result:
                    nf._request('GET', 'https://example.invalid')
                self.assertEqual(result.exception.transient, transient)
                self.assertNotIn('private-secret', str(result.exception))
        nf._tenant_token = lambda: 'fake'
        nf._request = Mock(return_value={'code':0})
        nf.reply_text('m', 'answer', request_uuid='stable')
        self.assertEqual(nf._request.call_args.args[2]['uuid'], 'stable')

    def test_inbox_permanent_failure_stops_temporary_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = listener.Inbox(Path(tmp)/'inbox.db')
            box = delivery.Outbox(Path(tmp)/'outbox.db')
            for mid, error, expected in [('temporary', TimeoutError(), 'pending'),
                                          ('permission', reliability.DeliveryError('no permission'), 'failed')]:
                inbox.add(mid, 'help')
                with patch.object(delivery, 'dispatch', side_effect=error):
                    listener.process_one(inbox, Mock(), box, tmp, {})
                with inbox._db() as db:
                    self.assertEqual(db.execute('SELECT status FROM incoming WHERE message_id=?',(mid,)).fetchone()[0], expected)


class RuntimeTests(unittest.TestCase):
    def test_process_lock_blocks_second_process_then_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'listener.lock'
            script = 'from agent.reliability import file_lock; import sys\nwith file_lock(sys.argv[1]): print("acquired")'
            with reliability.file_lock(path):
                result = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True,
                                        cwd=Path(__file__).resolve().parents[1])
                self.assertNotEqual(result.returncode, 0)
            result = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True,
                                    cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(result.returncode, 0)

    def test_queue_failure_recovers_and_dead_worker_restarts(self):
        called = threading.Event()
        process = Mock(side_effect=[sqlite3.OperationalError('locked'), False])
        sup = runtime.ListenerSupervisor(process, lambda: None, interval=.01)
        # No five-second test sleep; retain the production stop signal semantics.
        original_wait = sup.stopping.wait
        def wait(seconds):
            if process.call_count >= 2:
                called.set()
                sup.stopping.set()
            return original_wait(.001)
        with patch.object(sup.stopping, 'wait', side_effect=wait):
            sup.work()
        self.assertTrue(called.is_set())
        sup = runtime.ListenerSupervisor(lambda: False, lambda: None)
        dead = Mock()
        dead.is_alive.return_value = False
        sup.worker = dead
        sup.ensure_worker()
        self.assertIsNot(sup.worker, dead)
        worker = sup.worker
        sup.ensure_worker()
        self.assertIs(sup.worker, worker)  # busy/alive workers never duplicated
        sup.stop()


if __name__ == '__main__':
    unittest.main()
