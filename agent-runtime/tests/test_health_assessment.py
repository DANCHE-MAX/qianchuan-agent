"""Explicit assessment, durable coverage baseline and informational historical gaps."""
from pathlib import Path
from datetime import datetime,timedelta
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import health as H, hourly as h, monitor as m, settings, data_quality as q, collection_state as c

NOW=datetime(2026,10,29,2,10,tzinfo=h.CHINA)
ENV={'QC_ADVERTISER_IDS':'999','FEISHU_CHAT_ID':'oc_test','FEISHU_ALLOWED_CHAT_IDS':'oc_test oc_other'}


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'config').mkdir()
        cfg=settings.load_config();cfg['monitoring']['enabled']=True
        cfg['notify']['roles']={'运维':['ou_test']}
        (self.root/'config/rules.json').write_text(json.dumps(cfg),encoding='utf-8')
        (self.root/'config/daily_brief.json').write_text('{"enabled":false}',encoding='utf-8')
        self.nf=Mock();self.factory=Mock(return_value=self.nf)
        self.net=patch('urllib.request.urlopen',side_effect=AssertionError('No network'))
        self.net.start();self.addCleanup(self.net.stop)

    def touch(self,now):H.Store(self.root).put('999','listener','ok',now=now)

    def snap(self,at,now,kind='plan'):
        m.Store(self.root).save_snapshot({'account':'999','kind':kind,'day':at.date().isoformat(),
            'bucket':m.bucket(at).isoformat(),'fetched_at':now.isoformat(),'request_end':now.isoformat(),'rows':[]})

    def healthy(self,now=NOW):
        self.touch(now)
        for hour in range(now.hour):
            at=now.replace(hour=hour,minute=0,second=0,microsecond=0)
            h.Store(self.root).save({'advertiser_id':'999','start':at.isoformat(),
                                   'end':(at+timedelta(hours=1)).isoformat(),'fetched_at':now.isoformat(),'rows':[]})
        for i in range(now.hour*2+now.minute//30+1):
            at=now.replace(hour=i//2,minute=i%2*30,second=0,microsecond=0)
            for kind in ('plan','material'):self.snap(at,now,kind)
        m.Store(self.root).save_balance({'advertiser_id':'999','fetched_at':now.isoformat(),'amounts_yuan':{}})

    def remove_slot(self,at):
        with m.Store(self.root).db() as db:
            db.execute("DELETE FROM snapshots WHERE account='999' AND kind='plan' AND bucket=?",(at.isoformat(),))

    def check(self,now):
        self.touch(now);return H.check(self.root,ENV,self.factory,now,True)

    def active_gap(self):
        self.healthy();H.inspect(self.root,ENV,NOW)
        self.gap=NOW.replace(hour=0,minute=30);self.remove_slot(self.gap)
        self.check(NOW);self.check(NOW+timedelta(minutes=3))
        self.assertEqual(self.nf.send_card.call_count,1)
        self.nf.reset_mock()

    def has_active(self,code='intervals'):
        with H.Store(self.root).db() as db:
            row=db.execute('SELECT active FROM incidents WHERE account=? AND code=?',('999',code)).fetchone()
        return bool(row and row[0])

    def set_monitoring(self,enabled):
        path=self.root/'config/rules.json';cfg=json.loads(path.read_text(encoding='utf-8'))
        cfg['monitoring']['enabled']=enabled;path.write_text(json.dumps(cfg),encoding='utf-8')

    def test_user_case_hourly_complete_history19_snapshot_fresh(self):
        now=NOW.replace(hour=11,minute=39);self.healthy(now)
        with m.Store(self.root).db() as db:
            db.execute("DELETE FROM snapshots WHERE kind='plan' AND bucket<?",(now.replace(hour=9,minute=30).isoformat(),))
        value=H.inspect(self.root,ENV,now);quality=value['quality'];text=str(H.rendered(value))
        self.assertEqual(len(quality['historical_missing_slots']),19)
        self.assertEqual(quality['operational_missing_slots'],[])
        self.assertEqual(quality['components'],{'hourly':'complete','intervals':'limited','snapshot':'fresh'})
        self.assertNotIn('intervals',value['issues']);self.assertNotIn('当前检查项通过',text)
        self.assertIn('区间分析受限',text)

    def test_history_notice_once_across_restart_without_at(self):
        self.healthy()
        with m.Store(self.root).db() as db:db.execute("DELETE FROM snapshots WHERE kind='plan' AND bucket<?",(NOW.replace(hour=1,minute=0).isoformat(),))
        self.check(NOW);self.check(NOW+timedelta(minutes=1))
        H.Store(self.root).put('999','started','ok',now=NOW+timedelta(minutes=2))
        self.check(NOW+timedelta(minutes=3));self.check(NOW+timedelta(minutes=8))
        self.assertEqual(self.nf.send_card.call_count,1)
        self.assertIn('数据覆盖说明',str(self.nf.send_card.call_args));self.assertNotIn('<at ',str(self.nf.send_card.call_args))

    def test_startup_grace_keeps_active_incident_without_recovery(self):
        self.active_gap();at=NOW+timedelta(minutes=4)
        H.Store(self.root).put('999','started','ok',now=at)
        result=self.check(at)
        self.assertIn('intervals',result['deferred']);self.assertNotIn('intervals',result['verified'])
        self.assertTrue(self.has_active());self.nf.send_card.assert_not_called()
        self.assertIn('等待实际复核',str(H.rendered(result)))

    def test_grace_expiry_fault_still_active_no_fake_restore(self):
        self.active_gap();at=NOW+timedelta(minutes=4)
        H.Store(self.root).put('999','started','ok',now=at)
        self.check(at);self.check(at+timedelta(minutes=6))
        self.assertTrue(self.has_active());self.nf.send_card.assert_not_called()

    def test_monitor_pause_preserves_active_incident(self):
        self.active_gap();self.set_monitoring(False);value=self.check(NOW+timedelta(minutes=4))
        self.assertIn('intervals',value['deferred']);self.assertTrue(self.has_active());self.nf.send_card.assert_not_called()

    def test_resume_checks_old_runtime_gap_without_reset(self):
        self.active_gap();self.set_monitoring(False);self.check(NOW+timedelta(minutes=4))
        self.set_monitoring(True);result=self.check(NOW+timedelta(minutes=5))
        self.assertIn('intervals',result['issues']);self.assertTrue(self.has_active());self.nf.send_card.assert_not_called()

    def test_data_read_failure_does_not_recover_intervals(self):
        self.active_gap()
        with patch.object(q,'current',side_effect=sqlite3.DatabaseError('private')):
            value=self.check(NOW+timedelta(minutes=4))
        self.assertIn('data_read',value['issues']);self.assertNotIn('intervals',value['verified'])
        self.assertTrue(self.has_active());self.nf.send_card.assert_not_called()

    def test_coverage_read_failure_does_not_recover_intervals(self):
        self.active_gap()
        with patch.object(c,'sync',side_effect=sqlite3.DatabaseError('private')):
            value=self.check(NOW+timedelta(minutes=4))
        self.assertNotIn('intervals',value['verified']);self.assertTrue(self.has_active());self.nf.send_card.assert_not_called()

    def test_actual_fill_emits_one_specific_recovery(self):
        self.active_gap();self.snap(self.gap,NOW+timedelta(minutes=4))
        self.check(NOW+timedelta(minutes=4));self.check(NOW+timedelta(minutes=5))
        self.assertFalse(self.has_active());self.nf.send_card.assert_called_once()
        text=str(self.nf.send_card.call_args);self.assertIn('恢复通知',text);self.assertIn('运行期间半小时漏采',text)
        self.assertNotIn('已解除：intervals',text)

    def test_disabled_health_does_not_delete_incidents(self):
        self.active_gap();(self.root/'config/health.json').write_text('{"enabled":false}',encoding='utf-8')
        self.snap(self.gap,NOW);self.check(NOW+timedelta(minutes=4))
        self.assertTrue(self.has_active());self.nf.send_card.assert_not_called()

    def test_restart_preserves_first_collection_baseline(self):
        self.healthy();H.inspect(self.root,ENV,NOW);original=c.load(self.root,'999')
        with m.Store(self.root).db() as db:db.execute("DELETE FROM snapshots WHERE kind='plan' AND bucket<?",(NOW.replace(hour=1,minute=0).isoformat(),))
        H.Store(self.root).put('999','started','ok',now=NOW)
        H.inspect(self.root,ENV,NOW+timedelta(minutes=6))
        self.assertEqual(c.load(self.root,'999'),original)
        self.assertEqual(len(H.inspect(self.root,ENV,NOW+timedelta(minutes=6))['quality']['operational_missing_slots']),2)

    def test_paused_period_does_not_count_as_runtime_missing(self):
        self.healthy();H.inspect(self.root,ENV,NOW)
        self.set_monitoring(False);H.inspect(self.root,ENV,NOW+timedelta(minutes=10))
        self.set_monitoring(True);result=H.inspect(self.root,ENV,NOW.replace(hour=3,minute=10))
        self.assertIn(NOW.replace(hour=2,minute=30).isoformat(),result['quality']['paused_missing_slots'])
        self.assertNotIn(NOW.replace(hour=2,minute=30).isoformat(),result['quality']['operational_missing_slots'])

    def test_first_no_snapshot_baseline_and_later_misses(self):
        self.touch(NOW);H.inspect(self.root,ENV,NOW)
        value=H.inspect(self.root,ENV,NOW+timedelta(hours=1))
        self.assertEqual(len(value['quality']['operational_missing_slots']),2)
        self.assertEqual(len(value['quality']['historical_missing_slots']),4)

    def test_legacy_history_alarm_reclassified_without_recovery(self):
        self.healthy()
        with m.Store(self.root).db() as db:db.execute("DELETE FROM snapshots WHERE kind='plan' AND bucket<?",(NOW.replace(hour=1,minute=0).isoformat(),))
        with H.Store(self.root).db() as db:db.execute('INSERT INTO incidents VALUES(?,?,?,1)',('999','intervals',(NOW-timedelta(minutes=10)).isoformat()))
        self.check(NOW)
        self.assertFalse(self.has_active());self.assertEqual(self.nf.send_card.call_count,1)
        self.assertIn('数据覆盖说明',str(self.nf.send_card.call_args));self.assertNotIn('恢复通知',str(self.nf.send_card.call_args))

    def test_legacy_pending_recovery_never_replayed(self):
        self.healthy()
        with H.Store(self.root).db() as db:db.execute('INSERT INTO notices VALUES(?,?,?,?,0)',('legacy','999','oc_test',json.dumps(H.card('旧错误恢复',[]))))
        self.check(NOW);self.nf.send_card.assert_not_called()
        with H.Store(self.root).db() as db:self.assertEqual(db.execute("SELECT sent FROM notices WHERE id='legacy'").fetchone()[0],2)

    def test_day_rollover_not_historical_gap_recovery(self):
        self.active_gap();tomorrow=(NOW+timedelta(days=1)).replace(hour=0,minute=10)
        self.healthy(tomorrow);self.check(tomorrow)
        self.nf.send_card.assert_not_called();self.assertFalse(self.has_active())

    def test_readonly_health_command_does_not_send_advisory(self):
        self.healthy();H.check(self.root,ENV,self.factory,NOW,False);self.nf.send_card.assert_not_called()

    def test_unknown_collection_start_has_no_false_green(self):
        self.healthy()
        with m.Store(self.root).db() as db:db.execute("DELETE FROM snapshots WHERE kind='plan' AND bucket<?",(NOW.replace(hour=1,minute=0).isoformat(),))
        quality=q.current(self.root,ENV,NOW)
        self.assertFalse(quality['coverage_registered']);self.assertTrue(quality['unknown_missing_slots'])
        self.assertEqual(quality['state'],'attention');self.assertIn('尚未登记',q.summary(quality))

    def test_same_history_advisory_retries_uuid_after_timeout(self):
        self.healthy()
        with m.Store(self.root).db() as db:db.execute("DELETE FROM snapshots WHERE kind='plan' AND bucket<?",(NOW.replace(hour=1,minute=0).isoformat(),))
        self.nf.send_card.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.check(NOW)
        first=self.nf.send_card.call_args
        self.nf.send_card.side_effect=None;self.check(NOW+timedelta(minutes=1))
        self.assertEqual(first,self.nf.send_card.call_args)

    def test_two_accounts_have_independent_origins(self):
        self.healthy();H.inspect(self.root,ENV,NOW)
        c.sync(self.root,'888',NOW,True)
        self.assertNotEqual(c.load(self.root,'999'),c.load(self.root,'888'))

    def test_older_pause_assessment_cannot_close_newer_collection_period(self):
        self.healthy();c.sync(self.root,'999',NOW+timedelta(minutes=5),True)
        before=c.load(self.root,'999');c.sync(self.root,'999',NOW,False)
        self.assertEqual(c.load(self.root,'999'),before)

    def test_out_of_order_health_result_does_not_resolve_newer_fault(self):
        self.active_gap();status=H.inspect(self.root,ENV,NOW+timedelta(minutes=4))
        status['issues']={}
        with patch.object(H,'inspect',return_value=status):self.check(NOW+timedelta(minutes=2))
        self.assertTrue(self.has_active());self.nf.send_card.assert_not_called()

    def test_queued_recovery_waits_through_grace_for_actual_recheck(self):
        self.active_gap();self.snap(self.gap,NOW+timedelta(minutes=4))
        self.nf.send_card.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.check(NOW+timedelta(minutes=4))
        self.nf.reset_mock();self.nf.send_card.side_effect=None
        H.Store(self.root).put('999','started','ok',now=NOW+timedelta(minutes=5))
        self.check(NOW+timedelta(minutes=5));self.nf.send_card.assert_not_called()
        self.check(NOW+timedelta(minutes=11));self.nf.send_card.assert_called_once()
        self.assertIn('恢复通知',str(self.nf.send_card.call_args))

    def test_runtime_recovery_does_not_claim_historical_gap_filled(self):
        self.healthy();self.remove_slot(NOW.replace(hour=0,minute=0))
        self.check(NOW);self.nf.reset_mock()
        gap=NOW.replace(hour=1,minute=0);self.remove_slot(gap)
        self.check(NOW+timedelta(minutes=1));self.check(NOW+timedelta(minutes=4))
        self.nf.reset_mock();self.snap(gap,NOW+timedelta(minutes=5))
        result=self.check(NOW+timedelta(minutes=5))
        self.nf.send_card.assert_called_once();self.assertIn('恢复通知',str(self.nf.send_card.call_args))
        self.assertEqual(len(result['quality']['historical_missing_slots']),1)
        self.assertEqual(result['quality']['components']['intervals'],'limited')

    def test_retired_legacy_pending_alert_reconfirms_real_fault(self):
        self.active_gap()
        with H.Store(self.root).db() as db:
            db.execute('DELETE FROM notice_context')
            db.execute('UPDATE notices SET sent=0')
        self.check(NOW+timedelta(minutes=4));self.nf.send_card.assert_not_called()
        self.check(NOW+timedelta(minutes=7));self.nf.send_card.assert_called_once()
        self.assertIn('运行预警',str(self.nf.send_card.call_args))


if __name__=='__main__':unittest.main()
