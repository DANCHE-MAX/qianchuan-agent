"""No-network tests: four-hour scheduling, cross-midnight rates and runway."""
import copy
import json
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import balance_cycle as b, monitor as m, hourly as h, settings

NOW=datetime(2026,10,29,0,0,tzinfo=h.CHINA)
ENV={'QC_ADVERTISER_IDS':'999','FEISHU_CHAT_ID':'oc_test','QC_ACCESS_TOKEN':'sandbox'}


def sample(at,valid,account='999',frozen='0'):
    return {'advertiser_id':account,'fetched_at':at.isoformat(),'amounts_yuan':{
        'account_total':str(Decimal(str(valid))+Decimal(frozen)), 'account_valid':str(valid),'account_frozen':frozen}}


def window(at=NOW,valid='2000',step='50'):
    return [sample(at-timedelta(minutes=30*(8-i)),Decimal(valid)+Decimal(step)*(8-i)) for i in range(9)]


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'config').mkdir()
        cfg=settings.load_config();cfg['monitoring']['enabled']=True
        (self.root/'config/rules.json').write_text(json.dumps(cfg),encoding='utf-8')
        self.nf=Mock()
        self.network=patch('urllib.request.urlopen',side_effect=AssertionError('No real network'))
        self.network.start();self.addCleanup(self.network.stop)

    def tick(self,now,fail=False):
        values=window(now)
        for value in values[:-1]:m.Store(self.root).save_balance(value)
        fetch=lambda env,kind,at:{'account':'999','kind':kind,'day':at.date().isoformat(),
            'bucket':m.bucket(at).isoformat(),'fetched_at':at.isoformat(),'request_end':at.isoformat(),
            'topic':m.TOPICS[kind][0],'unit':'CNY','scope':m.gate.SCOPES[kind],'rows':[]}
        with patch.object(m,'fetch_snapshot',side_effect=fetch),patch.object(m.account_balance,'fetch',side_effect=TimeoutError() if fail else None,return_value=values[-1]):
            m.Engine(self.root,ENV,lambda chat:self.nf).tick(now)

    def test_production_uses_two_hour_baseline(self):
        values=window()
        value=m.balance_rate(values,values[-1])
        self.assertEqual(value['window_hours'],2)
        self.assertEqual(value['decline'],'200')
        self.assertIn('两小时',str(m.balance_notice(values[-1],value)))

    def test_new_even_hour_sends_summary(self):
        self.tick(NOW.replace(hour=14))
        self.nf.send_card.assert_called_once()
        self.assertIn('两小时',str(self.nf.send_card.call_args))

    def test_midnight_rate_uses_yesterday_20_to_today_00(self):
        values=window();value=b.rate(values,values[-1])
        self.assertEqual(Decimal(value['rate']),100)
        self.assertTrue(value['start'].startswith('2026-10-28T20:00'))
        text=str(b.notice(values[-1],value))
        self.assertIn('还能用约20.00小时',text);self.assertIn('暂不需要充值',text)

    def test_all_twelve_slots_send_one_each(self):
        for hour in b.HOURS:self.tick(NOW.replace(hour=hour))
        self.assertEqual(self.nf.send_card.call_count,12)

    def test_odd_hours_do_not_send(self):
        self.tick(NOW.replace(hour=9));self.tick(NOW.replace(hour=19))
        self.nf.send_card.assert_not_called()

    def test_restart_repeated_tick_does_not_duplicate(self):
        self.tick(NOW);self.tick(NOW+timedelta(minutes=1))
        self.assertEqual(self.nf.send_card.call_count,1)

    def test_after_ten_minute_window_no_stale_catchup(self):
        self.tick(NOW+timedelta(minutes=10));self.nf.send_card.assert_not_called()

    def test_utc_time_is_scheduled_in_shanghai(self):
        self.tick(NOW.astimezone(timezone.utc))
        self.nf.send_card.assert_called_once()

    def test_zero_rate_does_not_claim_infinite_runway(self):
        values=window(step='0');text=str(b.notice(values[-1],b.rate(values,values[-1])))
        self.assertIn('暂无法估算',text);self.assertNotIn('余额预计还能用约',text)

    def test_low_balance_red_and_reserve_reference(self):
        values=window(valid='500');rendered=b.notice(values[-1],b.rate(values,values[-1]))
        self.assertEqual(rendered['header']['template'],'red')
        self.assertIn('还能用约5.00小时',str(rendered));self.assertIn('及时充值',str(rendered))

    def test_no_current_balance_no_fabrication(self):
        with self.assertRaises(RuntimeError):self.tick(NOW,fail=True)
        self.assertIn('读取失败',str(self.nf.send_card.call_args))
        self.assertNotIn('还能用约',str(self.nf.send_card.call_args))

    def test_recharge_window_unknown(self):
        values=window();values[4]=sample(NOW-timedelta(hours=2),'9999')
        value=b.rate(values,values[-1]);self.assertIsNone(value['rate'])
        self.assertIn('无法估算',str(b.notice(values[-1],value)))

    def test_frozen_adjustment_unknown(self):
        values=window();values[4]['amounts_yuan']['account_frozen']='1'
        self.assertIsNone(b.rate(values,values[-1])['rate'])

    def test_missing_middle_sample_unknown(self):
        values=window();values.pop(4)
        self.assertIsNone(b.rate(values,values[-1])['rate'])

    def test_only_two_hours_not_used_as_four(self):
        values=window()[4:];self.assertIsNone(b.rate(values,values[-1])['rate'])

    def test_other_account_does_not_supply_baseline(self):
        values=window();values[0]['advertiser_id']='888'
        self.assertIsNone(b.rate(values,values[-1])['rate'])

    def test_actual_elapsed_not_forced_four(self):
        values=window();values[0]['fetched_at']=(NOW-timedelta(hours=4,minutes=3)).isoformat()
        value=b.rate(values,values[-1]);self.assertEqual(Decimal(value['rate']),Decimal(400)/Decimal('4.05'))

    def test_invalid_amount_or_naive_time_rejected(self):
        for change in ('money','time'):
            values=window()
            if change=='money':values[0]['amounts_yuan']['account_total']='NaN'
            else:values[0]['fetched_at']='2026-10-28T20:00:00'
            self.assertIsNone(b.rate(values,values[-1])['rate'])

    def test_old_pending_schedule_retired(self):
        m.Store(self.root).enqueue('balance:999:2026-10-29:0','999',NOW,m.card('旧两小时报告',[]))
        self.tick(NOW)
        self.nf.send_card.assert_called_once();self.assertNotIn('旧两小时报告',str(self.nf.send_card.call_args))

    def test_disabled_monitoring_is_silent(self):
        cfg=settings.load_config();cfg['monitoring']['enabled']=False
        (self.root/'config/rules.json').write_text(json.dumps(cfg),encoding='utf-8')
        self.tick(NOW);self.nf.send_card.assert_not_called()

    def test_zero_or_negative_available_shows_zero_runway(self):
        for available in ('0','-10'):
            values=window(valid=available)
            self.assertIn('预计可用时长：0小时',str(b.notice(values[-1],b.rate(values,values[-1]))))

    def test_no_samples_but_zero_balance_still_zero_runway(self):
        current=sample(NOW,'0')
        self.assertIn('预计可用时长：0小时',str(b.notice(current,b.rate([],current))))

    def test_tiny_positive_rate_not_rounded_to_zero(self):
        values=window(step='0.001')
        self.assertIn('低于0.01元/小时',str(b.notice(values[-1],b.rate(values,values[-1]))))


if __name__=='__main__':unittest.main()

# analysis-admission-v26
