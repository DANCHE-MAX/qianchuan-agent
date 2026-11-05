"""Offline evidence tests: no credentials, API requests or group messages."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import health, listener, notify, qc_auth, reliability


def events(output):
    return [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]


class EvidenceTests(unittest.TestCase):
    def test_task_success_records_start_end_and_preserves_result(self):
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()) as output:
            value = object()
            self.assertIs(health.run(root, {'QC_ADVERTISER_IDS': '999'}, '小时采集', lambda: value), value)
            records = events(output)
            self.assertEqual([r['event'] for r in records], ['task_start', 'task_success'])
            self.assertEqual(records[-1]['task'], '小时采集')
            self.assertGreaterEqual(records[-1]['elapsed_seconds'], 0)
            self.assertEqual(datetime.fromisoformat(records[-1]['time']).utcoffset(), timedelta(hours=8))
            self.assertEqual(health.Store(root).signals('999')['小时采集']['state'], 'ok')

    def test_task_failure_logs_class_not_secret_and_preserves_failure_state(self):
        failure = ValueError('private-token-must-not-appear')
        def action():
            raise failure
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(ValueError) as caught:
                health.run(root, {'QC_ADVERTISER_IDS': '999'}, '小时采集', action)
            self.assertIs(caught.exception, failure)
            self.assertEqual([r['event'] for r in events(output)], ['task_start', 'task_failure'])
            self.assertEqual(events(output)[-1]['error_type'], 'ValueError')
            self.assertNotIn('private-token', output.getvalue())
            self.assertEqual(health.Store(root).signals('999')['小时采集']['state'], 'failed')

    def test_qianchuan_business_error_retains_numeric_code_without_body(self):
        with redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(reliability.ServiceError):
                qc_auth.call({'QC_ACCESS_TOKEN': 'secret-access'}, lambda token: {'code': 40002, 'message': 'private-secret'})
        self.assertTrue(events(output), 'API failure needs a structured event')
        record = events(output)[-1]
        self.assertEqual((record['event'], record['api_code']), ('api_failure', 40002))
        self.assertEqual(record['operation'], 'qianchuan.request')
        self.assertNotIn('secret', output.getvalue())

    def test_qianchuan_http_failure_preserves_status_without_url(self):
        def action(token):
            raise urllib.error.HTTPError('https://example.invalid/?secret=hidden', 403, 'private-secret', {}, io.BytesIO(b'{}'))
        with redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(reliability.ServiceError):
                qc_auth.call({'QC_ACCESS_TOKEN': 'secret-access'}, action)
        self.assertTrue(events(output), 'HTTP failure needs a structured event')
        self.assertEqual(events(output)[-1]['http_status'], 403)
        self.assertNotIn('secret', output.getvalue())

    def test_feishu_send_success_is_logged_without_content_or_token(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"code":0,"data":{"message_id":"secret-id"}}'
        nf = notify.FeishuAppNotifier('private-app', 'private-secret', 'private-chat')
        nf._tenant_token = lambda: 'private-token'
        with patch('urllib.request.urlopen', return_value=Response()), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(nf.send_text('private-content')['code'], 0)
        self.assertTrue(events(output), 'Sending must leave success evidence')
        self.assertEqual(events(output)[-1]['event'], 'api_success')
        self.assertEqual(events(output)[-1]['operation'], 'feishu.send')
        self.assertNotIn('private', output.getvalue())
        self.assertNotIn('secret', output.getvalue())

    def test_feishu_http_error_logs_numeric_business_code_only(self):
        nf = notify.FeishuAppNotifier('private-app', 'private-secret', 'private-chat')
        nf._tenant_token = lambda: 'private-token'
        error = urllib.error.HTTPError('https://example.invalid/?secret=hidden', 400, 'private', {},
                                       io.BytesIO(b'{"code":230001,"msg":"private-secret"}'))
        with patch('urllib.request.urlopen', side_effect=error), redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(reliability.DeliveryError):
                nf.send_text('private-content')
        self.assertTrue(events(output), 'Sending must leave failure evidence')
        self.assertEqual(events(output)[-1]['event'], 'api_failure')
        self.assertEqual(events(output)[-1]['http_status'], 400)
        self.assertEqual(events(output)[-1]['api_code'], 230001)
        self.assertNotIn('private', output.getvalue())
        self.assertNotIn('secret', output.getvalue())

    def diagnostics(self):
        spec = importlib.util.find_spec('agent.diagnostics')
        self.assertIsNotNone(spec, 'Shared safe runtime logger is missing')
        return importlib.import_module('agent.diagnostics')

    def test_gap_records_225_minutes_once_without_claiming_freeze(self):
        diagnostics = self.diagnostics()
        tracker = diagnostics.GapTracker()
        start = datetime(2026, 10, 30, 4, tzinfo=timezone(timedelta(hours=8)))
        with redirect_stdout(io.StringIO()) as output:
            tracker.observe(start)
            tracker.observe(start + timedelta(minutes=225))
            tracker.observe(start + timedelta(minutes=226))
        self.assertEqual(len(events(output)), 1)
        record = events(output)[0]
        self.assertEqual((record['event'], record['gap_seconds']), ('scheduler_gap', 13500))
        self.assertNotIn('freeze', output.getvalue())

    def test_backward_clock_is_reported_and_resets_baseline(self):
        diagnostics = self.diagnostics()
        tracker = diagnostics.GapTracker()
        start = datetime(2026, 10, 30, 4, tzinfo=timezone.utc)
        with redirect_stdout(io.StringIO()) as output:
            tracker.observe(start)
            tracker.observe(start - timedelta(minutes=10))
            tracker.observe(start - timedelta(minutes=9))
        self.assertEqual([r['event'] for r in events(output)], ['clock_regression'])

    def test_untrusted_code_values_and_unknown_fields_are_not_logged(self):
        diagnostics = self.diagnostics()
        with redirect_stdout(io.StringIO()) as output:
            diagnostics.emit('api_failure', operation='qianchuan.request', api_code='private-secret',
                             http_status={'secret': 'hidden'}, token='private-token', payload='private-content')
        self.assertNotIn('private', output.getvalue())
        self.assertNotIn('hidden', output.getvalue())
        self.assertNotIn('api_code', events(output)[0])

    def test_reaction_is_acknowledged_without_reply_or_event_content(self):
        self.assertTrue(hasattr(listener, 'build_event_handler'), 'Handler registration boundary is missing')
        callbacks = {}
        class Builder:
            def register_p2_im_message_receive_v1(self, cb): callbacks['message'] = cb; return self
            def register_p2_im_message_reaction_created_v1(self, cb): callbacks['reaction'] = cb; return self
            def build(self): return self
        sdk = SimpleNamespace(EventDispatcherHandler=SimpleNamespace(builder=lambda *args: Builder()))
        replies = []
        listener.build_event_handler(sdk, lambda event: replies.append(event))
        with redirect_stdout(io.StringIO()) as output:
            callbacks['reaction']({'private-content': 'private-token'})
        self.assertEqual(replies, [])
        self.assertEqual(events(output)[0]['event'], 'reaction_ignored')
        self.assertNotIn('private', output.getvalue())
        callbacks['message']('ordinary-message')
        self.assertEqual(replies, ['ordinary-message'])

    def test_closed_stdout_does_not_change_completed_api_result(self):
        diagnostics = self.diagnostics()
        closed = io.StringIO(); closed.close()
        with redirect_stdout(closed):
            result = diagnostics.request('qianchuan.request', lambda: {'code': 0})
        self.assertEqual(result, {'code': 0})
        failure = RuntimeError('original-action-error')
        def action(): raise failure
        with redirect_stdout(closed):
            with self.assertRaises(RuntimeError) as caught:
                diagnostics.request('feishu.send', action)
        self.assertIs(caught.exception, failure)

    def test_malformed_refresh_result_is_not_logged_as_success(self):
        diagnostics = self.diagnostics()
        with redirect_stdout(io.StringIO()) as output:
            result = diagnostics.request('qianchuan.refresh', lambda: None)
        self.assertIsNone(result, 'Diagnostics must not change the transport return value')
        self.assertEqual(events(output)[0]['event'], 'api_failure')

    def test_error_code_strings_never_enter_displayable_qianchuan_errors(self):
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(reliability.ServiceError) as caught:
                qc_auth.call({'QC_ACCESS_TOKEN': 'fake-token'}, lambda token: {'code': 'private-secret'})
            self.assertNotIn('private-secret', str(caught.exception))

    def test_refresh_error_code_strings_never_enter_displayable_errors(self):
        with redirect_stdout(io.StringIO()):
            requests = []
            def request(env):
                requests.append(env['QC_APP_ID'])
                return {'code': 'private-secret'}
            manager = qc_auth.TokenManager({'QC_APP_ID': '1', 'QC_APP_SECRET': 'fake-secret',
                                           'QC_REFRESH_TOKEN': 'fake-refresh'},
                                          request=request)
            with self.assertRaises(reliability.ServiceError) as caught:
                manager.token(force=True)
            self.assertNotIn('private-secret', str(caught.exception))
            self.assertEqual(requests, ['1'])

    def test_error_code_strings_never_enter_displayable_feishu_errors(self):
        nf = notify.FeishuAppNotifier('fake-app', 'fake-secret', 'fake-chat')
        error = urllib.error.HTTPError('https://example.invalid', 400, 'private', {},
                                       io.BytesIO(b'{"code":"private-secret","msg":"private-secret"}'))
        with patch('urllib.request.urlopen', side_effect=error), redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(reliability.DeliveryError) as caught:
                nf._request('POST', nf.BASE+'/im/v1/messages')
        self.assertNotIn('private-secret', str(caught.exception))
        self.assertNotIn('private-secret', output.getvalue())


if __name__ == '__main__':
    unittest.main()
