"""No-network fault simulations for v23; no live Feishu or ad calls."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agent import health as H, hourly as h, monitor as m, data_quality as q, settings, listener, daily_brief
from test_daily_brief import fixtures, NOW, ENV


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'config').mkdir()
        cfg=settings.load_config();cfg['monitoring']['enabled']=True
        (self.root/'config/rules.json').write_text(json.dumps(cfg),encoding='utf-8')
        self.now=NOW.replace(hour=2,minute=10);self.factory=Mock(return_value=Mock())
        self.net=patch('urllib.request.urlopen',side_effect=AssertionError('No live network'))
        self.net.start();self.addCleanup(self.net.stop)

    def healthy(self, now=None):
        now=now or self.now;store=H.Store(self.root)
        store.put('999','listener','ok',now=now)
        for i in range(now.hour):
            start=now.replace(hour=i,minute=0,second=0,microsecond=0)
            h.Store(self.root).save({'advertiser_id':'999','start':start.isoformat(),
                'end':(start+timedelta(hours=1)).isoformat(),'fetched_at':now.isoformat(),'rows':[]})
        for kind in ('plan','material'):
            for i in range(now.hour*2+now.minute//30+1):
                slot=now.replace(hour=i//2,minute=i%2*30,second=0,microsecond=0)
                m.Store(self.root).save_snapshot({'account':'999','kind':kind,'day':now.date().isoformat(),
                    'bucket':slot.isoformat(),'fetched_at':now.isoformat(),'request_end':now.isoformat(),'rows':[]})
        m.Store(self.root).save_balance({'advertiser_id':'999','fetched_at':now.isoformat(),'amounts_yuan':{}})

    def test_good_state_is_silent(self):
        self.healthy();result=H.check(self.root,ENV,self.factory,self.now,True)
        self.assertEqual(result['issues'],{});self.factory.assert_not_called()

    def test_missing_heartbeat_delayed_once_and_restart_dedup(self):
        self.healthy()
        with H.Store(self.root).db() as db:db.execute("DELETE FROM signals WHERE name='listener'")
        H.check(self.root,ENV,self.factory,self.now,True);self.factory.assert_not_called()
        H.check(self.root,ENV,self.factory,self.now+timedelta(minutes=3),True)
        self.assertEqual(self.factory.return_value.send_card.call_count,1)
        H.check(self.root,ENV,self.factory,self.now+timedelta(minutes=4),True)
        self.assertEqual(self.factory.return_value.send_card.call_count,1)

    def test_recovery_notifies_once(self):
        self.healthy()
        H.Store(self.root).put('999','授权维护','failed','ServiceError',self.now)
        H.check(self.root,ENV,self.factory,self.now,True)
        end=self.now+timedelta(minutes=3);self.healthy(end)
        H.check(self.root,ENV,self.factory,end,True)
        H.Store(self.root).put('999','授权维护','ok',now=end)
        H.check(self.root,ENV,self.factory,end,True)
        H.check(self.root,ENV,self.factory,end,True)
        self.assertEqual(self.factory.return_value.send_card.call_count,2)
        self.assertIn('恢复通知',str(self.factory.return_value.send_card.call_args))

    def test_delivery_timeout_reuses_uuid(self):
        self.healthy();H.Store(self.root).put('999','授权维护','failed','TimeoutError',self.now)
        H.check(self.root,ENV,self.factory,self.now,True)
        self.factory.return_value.send_card.side_effect=TimeoutError('secret')
        with self.assertRaises(TimeoutError):H.check(self.root,ENV,self.factory,self.now+timedelta(minutes=3),True)
        first=self.factory.return_value.send_card.call_args
        self.factory.return_value.send_card.side_effect=None
        H.check(self.root,ENV,self.factory,self.now+timedelta(minutes=3),True)
        self.assertEqual(first,self.factory.return_value.send_card.call_args)

    def test_bound_chat_never_silently_changes(self):
        self.healthy();H.Store(self.root).put('999','授权维护','failed','ServiceError',self.now)
        H.check(self.root,ENV,self.factory,self.now,True)
        self.factory.return_value.send_card.side_effect=TimeoutError()
        end=self.now+timedelta(minutes=3)
        with self.assertRaises(TimeoutError):H.check(self.root,ENV,self.factory,end,True)
        with self.assertRaises(ValueError):H.check(self.root,dict(ENV,FEISHU_HEALTH_CHAT_ID='oc_second'),self.factory,end,True)

    def test_forbidden_group_rejected(self):
        with self.assertRaises(ValueError):H.check(self.root,dict(ENV,FEISHU_HEALTH_CHAT_ID='oc_bad'),self.factory,self.now,True)
        self.factory.assert_not_called()

    def test_missing_hours_not_zero_and_empty_hours_are_valid(self):
        result=q.current(self.root,ENV,self.now)
        self.assertEqual(result['missing_hours'],['00:00','01:00'])
        self.healthy();result=q.current(self.root,ENV,self.now)
        self.assertEqual(result['covered_hours'],2);self.assertEqual(result['missing_hours'],[])

    def test_other_account_does_not_fill_missing_hour(self):
        h.Store(self.root).save({'advertiser_id':'888','start':self.now.replace(hour=0,minute=0).isoformat(),
                                'end':self.now.replace(hour=1,minute=0).isoformat(),'rows':[]})
        self.assertEqual(q.current(self.root,ENV,self.now)['covered_hours'],0)

    def test_old_snapshot_has_cutoff_and_warning(self):
        self.healthy(self.now-timedelta(minutes=50))
        result=q.current(self.root,ENV,self.now)
        self.assertGreater(result['snapshot_age_minutes'],45)
        self.assertIn('过期',json.dumps(result,ensure_ascii=False))

    def test_invalid_snapshot_nan_or_duplicate_is_rejected(self):
        self.healthy();store=m.Store(self.root);value=store.snapshots('999','plan',self.now.date().isoformat())[-1]
        row={'id':'1',**{key:'0' for key in m.METRICS}};row['gmv']='NaN';value['rows']=[row]
        store.save_snapshot(value)
        self.assertEqual(q.current(self.root,ENV,self.now)['invalid_records'],1)
        row['gmv']='0';value['rows']=[row,row];store.save_snapshot(value)
        self.assertEqual(q.current(self.root,ENV,self.now)['invalid_records'],1)

    def test_no_early_snapshot_does_not_remove_cumulative_totals(self):
        self.healthy()
        with m.Store(self.root).db() as db:db.execute('DELETE FROM snapshots WHERE bucket<?',(m.bucket(self.now).isoformat(),))
        value=q.current(self.root,ENV,self.now)
        self.assertTrue(value['missing_snapshot_slots'])
        self.assertEqual(value['state'],'attention')
        self.assertNotIn('intervals',H.inspect(self.root,ENV,self.now)['issues'])

    def test_daily_overdue_detected_even_no_job(self):
        now=self.now.replace(hour=10,minute=16);self.healthy(now)
        self.assertIn('daily',H.inspect(self.root,ENV,now)['issues'])
        dbpath=self.root/'data/qianchuan/daily_brief.db'
        with sqlite3.connect(dbpath) as db:
            db.execute('CREATE TABLE jobs(account TEXT,day TEXT,sent INTEGER,attempts INTEGER)')
            db.execute('INSERT INTO jobs VALUES(?,?,1,0)',('999','2026-10-28'))
        db.close()
        self.assertNotIn('daily',H.inspect(self.root,ENV,now)['issues'])

    def test_daily_account_mismatch_or_sum_mismatch_stops_report(self):
        rows,meta=fixtures();cfg=settings.load_config()['model']
        result=daily_brief.model.evaluate(rows,dict(cfg,window_days=1),cfg['season'],'2026-10-28',today=NOW.date())
        result['source_api']=meta
        self.assertEqual(q.daily(result,'999','2026-10-28')['state'],'checked')
        with self.assertRaises(ValueError):q.daily(result,'888','2026-10-28')
        result['totals']['gmv']='1'
        with self.assertRaises(ValueError):q.daily(result,'999','2026-10-28')

    def test_task_error_sanitized_and_success_clears(self):
        def bad():raise ValueError('access_token=do-not-store')
        with self.assertRaises(ValueError):H.run(self.root,ENV,'授权维护',bad)
        self.assertNotIn('do-not-store',json.dumps(H.Store(self.root).signals('999')))
        H.run(self.root,ENV,'授权维护',lambda:None)
        self.assertEqual(H.Store(self.root).signals('999')['授权维护']['state'],'ok')

    def test_inflight_retry_preserves_unresolved_failure(self):
        H.Store(self.root).put('999','授权维护','failed','ServiceError',self.now)
        def retry():self.assertEqual(H.Store(self.root).signals('999')['授权维护']['state'],'failed')
        H.run(self.root,ENV,'授权维护',retry)

    def test_check_command_bypasses_llm(self):
        self.healthy();notifier=Mock()
        with patch.object(H,'check',return_value=H.inspect(self.root,ENV,self.now)):
            listener.reply_command(notifier,'m','健康检查',outdir=self.root/'out',env=ENV)
        notifier.reply_card.assert_called_once()

    def test_startup_grace_does_not_hide_dead_process(self):
        H.Store(self.root).put('999','started','ok',now=self.now)
        issues=H.inspect(self.root,ENV,self.now)['issues']
        self.assertIn('listener',issues);self.assertNotIn('hours',issues)

    def test_disabled_monitoring_avoids_snapshot_balance_alerts(self):
        cfg=settings.load_config();cfg['monitoring']['enabled']=False
        (self.root/'config/rules.json').write_text(json.dumps(cfg),encoding='utf-8')
        issues=H.inspect(self.root,ENV,self.now)['issues']
        self.assertNotIn('snapshot:plan',issues);self.assertNotIn('balance',issues)

    def test_future_snapshot_detected(self):
        self.healthy(self.now+timedelta(minutes=6))
        self.assertGreater(q.current(self.root,ENV,self.now)['invalid_records'],0)

    def test_corrupt_business_database_still_yields_health_incident(self):
        self.healthy()
        m.Store(self.root).path.write_bytes(b'not sqlite')
        value=H.inspect(self.root,ENV,self.now)
        self.assertIn('data_read',value['issues']);self.assertIn('balance_read',value['issues'])

    def test_dead_worker_and_stalled_progress_recorded(self):
        thread=Mock();thread.is_alive.return_value=False
        engine=H.Engine(self.root,ENV,self.factory,{'消息处理':lambda:thread},worker_tick=lambda:H.time.monotonic()-H.options(self.root)['task_timeout_minutes']*60-1)
        with patch.object(H,'check',side_effect=lambda *a,**k:engine.stopping.set()):engine.work()
        signals=H.Store(self.root).signals('999')
        self.assertEqual(signals['线程:消息处理']['state'],'failed')
        self.assertEqual(signals['消息处理进度']['state'],'failed')


if __name__=='__main__':unittest.main()

# health-assessment-v25
