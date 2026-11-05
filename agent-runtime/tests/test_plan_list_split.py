"""t80u offline: plan-list time pair, paging, independent report/list outcomes, API labels."""
from contextlib import redirect_stdout
import io, json, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import hourly as h, health, diagnostics, qc_auth
from agent.reliability import ServiceError

NOW = datetime(2026, 10, 30, 11, 12, tzinfo=h.CHINA)
ENV = {'QC_ADVERTISER_IDS': '999', 'QC_ACCESS_TOKEN': 'test-secret-token'}


def item(pid, status='DELIVERY_OK', opt='ENABLE'):
    return {'ad_info': {'id': pid, 'name': 'p' + pid, 'status': status, 'opt_status': opt}}


def page(items, p, count, pages):
    return {'code': 0, 'data': {'ad_list': items, 'page_info': {'page': p, 'total_num': count, 'total_page': pages}}}


def hour_value(start):
    return {'advertiser_id': '999', 'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat(),
            'fetched_at': NOW.isoformat(), 'rows': [], 'pages': []}


def events(out):
    return [json.loads(x) for x in out.getvalue().splitlines() if x.startswith('{')]


class Tests(unittest.TestCase):
    def test_time_pair_on_every_page_and_paused_history_kept(self):
        req = Mock(side_effect=[page([item('1'), item('2', 'DISABLE', 'DISABLE')], 1, 3, 2),
                                page([item('3', 'TIME_DONE', 'DISABLE')], 2, 3, 2)])
        got = h.fetch_plan_list(ENV, '2026-10-30', request=req)
        self.assertEqual([x['ad_info']['status'] for x in got], ['DELIVERY_OK', 'DISABLE', 'TIME_DONE'])
        self.assertEqual(req.call_count, 2)
        for c in req.call_args_list:
            p = c.args[0]
            self.assertEqual((p['start_time'], p['end_time']), ('2026-10-30 00:00:00', '2026-10-30 23:59:59'))
            self.assertEqual(p['filtering'], {'status': 'ALL'})
            self.assertNotIn('adlab_scene', p); self.assertNotIn('data_period', p)
        self.assertEqual([c.args[0]['page'] for c in req.call_args_list], [1, 2])

    def test_invalid_day_and_inconsistent_paging_rejected(self):
        for bad in ('2026-1-3', '20261030', '2026-10-30T00:00'):
            with self.assertRaises(ValueError): h.plan_list_window(bad)
        req = Mock(side_effect=[page([item('1')], 1, 2, 2), page([item('2')], 2, 3, 2)])
        with self.assertRaises(ValueError): h.fetch_plan_list(ENV, '2026-10-30', request=req)

    def run_tick(self, root, hour_ok, list_ok):
        c = h.Collector(root, ENV)
        fh = (lambda env, start, now=None: hour_value(start)) if hour_ok else Mock(side_effect=ServiceError('x', api_code=40000))
        fl = Mock(return_value=[item('1'), item('2', 'DISABLE', 'DISABLE')]) if list_ok else Mock(side_effect=ServiceError('x', api_code=40000))
        with patch.object(h, 'fetch_hour', side_effect=fh), patch.object(h, 'fetch_plan_list', fl), patch.object(h, 'now_china', return_value=NOW):
            return lambda: health.run(root, ENV, '小时采集', lambda: c.tick(now=NOW))

    def test_reports_ok_list_failed_is_not_collection_failure(self):
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()) as out:
            c = h.Collector(root, ENV)
            with patch.object(h, 'fetch_hour', side_effect=lambda env, start, now=None: hour_value(start)), \
                 patch.object(h, 'fetch_plan_list', side_effect=ServiceError('x', api_code=40000)):
                health.run(root, ENV, '小时采集', lambda: c.tick(now=NOW))
            sig = health.Store(root).signals('999')
            self.assertEqual(sig['小时采集']['state'], 'ok')
            self.assertEqual(sig['小时报表']['state'], 'ok')
            self.assertEqual((sig['状态列表']['state'], sig['状态列表']['detail']), ('failed', 'ServiceError'))
            self.assertEqual(len(h.Store(root).hours('999', '2026-10-30')), 11)
            self.assertIsNone(h.Store(root).plans('999'))
            sub = health.subresults(root, '999', sig)
            self.assertEqual(sub['小时报表']['state'], 'ok'); self.assertIsNotNone(sub['小时报表']['data_updated_at'])
            self.assertEqual(sub['状态列表']['state'], 'failed'); self.assertIsNone(sub['状态列表']['data_updated_at'])
            self.assertNotIn('test-secret-token', out.getvalue())
            # next minute: list recovers, old failure cleared, update time recorded
            with patch.object(h, 'fetch_hour', side_effect=AssertionError('no refetch')), \
                 patch.object(h, 'fetch_plan_list', return_value=[item('1')]):
                health.run(root, ENV, '小时采集', lambda: c.tick(now=NOW))
            sig = health.Store(root).signals('999')
            self.assertEqual(sig['状态列表']['state'], 'ok')
            self.assertIsNotNone(health.subresults(root, '999', sig)['状态列表']['data_updated_at'])

    def test_report_failure_independent_of_list_success(self):
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()):
            c = h.Collector(root, ENV)
            with patch.object(h, 'fetch_hour', side_effect=ServiceError('x', api_code=40000)), \
                 patch.object(h, 'fetch_plan_list', return_value=[item('1')]):
                with self.assertRaises(RuntimeError):
                    health.run(root, ENV, '小时采集', lambda: c.tick(now=NOW))
            sig = health.Store(root).signals('999')
            self.assertEqual(sig['小时报表']['state'], 'failed'); self.assertEqual(sig['小时采集']['state'], 'failed')
            self.assertEqual(sig['状态列表']['state'], 'ok')
            self.assertEqual(len(h.Store(root).plans('999')['plans']), 1)

    def test_old_generic_failure_cleared_and_specific_message(self):
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()):
            store = health.Store(root)
            store.put('999', '小时采集', 'failed', 'ServiceError')  # legacy V1 signal
            c = h.Collector(root, ENV)
            with patch.object(h, 'fetch_hour', side_effect=lambda env, start, now=None: hour_value(start)), \
                 patch.object(h, 'fetch_plan_list', return_value=[item('1')]):
                health.run(root, ENV, '小时采集', lambda: c.tick(now=NOW))
            self.assertEqual(store.signals('999')['小时采集']['state'], 'ok')
        src = Path(health.__file__).read_text(encoding='utf-8')
        self.assertIn('计划状态列表刷新失败', src)

    def test_api_label_allowlisted_and_no_secrets(self):
        out = io.StringIO()
        with redirect_stdout(out):
            with diagnostics.api_label('qianchuan.plan_list'):
                diagnostics.emit('api_failure', operation='qianchuan.request', api_code=40000, error_type='ServiceError')
                diagnostics.emit('task_failure', task='状态列表')
            with diagnostics.api_label('https://x/?access_token=abc'):
                diagnostics.emit('api_success', operation='qianchuan.request', api_code=0)
            diagnostics.emit('api_success', operation='qianchuan.request', api='evil', api_code=0)
        rec = events(out)
        self.assertEqual(rec[0]['api'], 'qianchuan.plan_list')
        self.assertNotIn('api', rec[1]); self.assertEqual(rec[1]['task'], '状态列表')
        self.assertNotIn('api', rec[2]); self.assertNotIn('api', rec[3])
        self.assertNotIn('abc', out.getvalue())

    def test_real_call_path_labels_both_apis(self):
        out = io.StringIO()
        class M:
            def token(self, rejected=None): return 'test-secret-token'
        req = Mock(return_value=page([item('1')], 1, 1, 1))
        with patch.object(qc_auth, 'manager', return_value=M()), redirect_stdout(out):
            h.fetch_plan_list(ENV, '2026-10-30', request=req)
            with self.assertRaises(ValueError):
                h.fetch_hour(ENV, NOW.replace(hour=10, minute=0), now=NOW, request=Mock(return_value={'code': 0, 'data': {}}))
        apis = [r.get('api') for r in events(out) if r['event'].startswith('api_')]
        self.assertEqual(apis, ['qianchuan.plan_list', 'qianchuan.hourly_report'])
        self.assertNotIn('test-secret-token', out.getvalue())


    def test_report_update_time_is_max_validated_fetched_at(self):
        with tempfile.TemporaryDirectory() as root:
            st = h.Store(root)
            ten = NOW.replace(hour=10, minute=0); three = NOW.replace(hour=3, minute=0)
            st.save(dict(hour_value(ten), fetched_at=NOW.replace(hour=11, minute=1).isoformat()))
            st.save(dict(hour_value(three), fetched_at=NOW.replace(hour=11, minute=12).isoformat()))  # back-filled later
            with st.connect() as db:  # invalid rows are ignored, never treated as an update
                db.execute('INSERT INTO hours VALUES(?,?,?)', ('999', 'x', json.dumps({'start': 'x', 'fetched_at': '2099-01-01T00:00:00+08:00'})))
                db.execute('INSERT INTO hours VALUES(?,?,?)', ('999', 'y', '{bad'))
            sub = health.subresults(root, '999', {})
            self.assertEqual(sub['小时报表']['data_updated_at'], NOW.replace(hour=11, minute=12).isoformat())
            self.assertEqual(sub['小时报表']['latest_hour'], ten.isoformat())

if __name__ == '__main__':
    unittest.main()
