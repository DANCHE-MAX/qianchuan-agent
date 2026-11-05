"""Sandbox-only: real calculation/persistence, mocked API and Feishu transport."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import daily_brief as d, settings, listener

NOW = datetime(2026,10,29,10,0,tzinfo=d.hourly.CHINA)
ENV = {'QC_ADVERTISER_IDS':'999','QC_ACCESS_TOKEN':'sandbox-only',
       'FEISHU_CHAT_ID':'oc_sandbox','FEISHU_ALLOWED_CHAT_IDS':'oc_sandbox oc_second'}


def fixtures(day='2026-10-28'):
    rows = [{'计划ID':pid,'计划名称':name,'日期':day,'小时区间':'全部','整体成交金额':gmv,
             '整体消耗':spend,'净成交金额':net,'综合成本':spend}
            for pid,name,gmv,spend,net in [('9000000000000006','托管计划','6000','600','4000'),
                                          ('9000000000000008','马甲计划','4000','400','3000')]]
    meta={'advertiser_id':'999','start':day,'end':day,'days':1,'fetched_at':(datetime.fromisoformat(day).replace(tzinfo=d.hourly.CHINA)+timedelta(days=1,hours=10)).isoformat(),
          'pages':[],'data_topic':d.qianchuan.TOPIC,'monetary_unit':'CNY'}
    return rows,meta


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'config').mkdir()
        cfg=settings.load_config()
        cfg['model']['plans'] = {'9000000000000006': {'refund_band': 'overall', 'basis': 'synthetic test fixture'}, '9000000000000008': {'refund_band': 'gt300', 'basis': 'synthetic test fixture'}}
        (self.root/'config/rules.json').write_text(json.dumps(cfg),encoding='utf-8')
        self.notifier=Mock();self.factory=Mock(return_value=self.notifier)
        # Any unmocked network call is an immediate test failure.
        self.network=patch('urllib.request.urlopen',side_effect=AssertionError('Sandbox: real network forbidden'))
        self.network.start();self.addCleanup(self.network.stop)
        self.api=patch.object(d.qianchuan,'fetch_daily',return_value=fixtures())
        self.fetch=self.api.start();self.addCleanup(self.api.stop)

    def engine(self,env=None): return d.Engine(self.root,env or ENV,self.factory)

    def test_exact_10_fetches_yesterday_and_sends_one_summary(self):
        self.assertTrue(self.engine().tick(NOW))
        self.fetch.assert_called_once_with(ENV,'2026-10-28',days=1,today=NOW.date())
        self.notifier.send_card.assert_called_once()
        payload=self.notifier.send_card.call_args.args[0];text=json.dumps(payload,ensure_ascii=False)
        self.assertIn('2026-10-28',text);self.assertIn('10,000.00',text);self.assertIn('1,000.00',text)
        self.assertIn('10.00',text);self.assertIn('不代表全部商品视频全域',text)
        self.assertEqual(payload['header']['title']['content'],'千川投流｜昨日重点日报')
        self.factory.assert_called_once_with('oc_sandbox')
        saved=listener._latest_report(self.root/'out',account='999')
        self.assertEqual(saved['audit']['end'],'2026-10-28')
        self.assertEqual(saved['totals']['commission_cost'],'0')
        self.assertEqual(len(saved['plans']),2)

    def test_before_10_outside_catchup_and_disabled_do_not_send(self):
        engine=self.engine()
        for now in (NOW-timedelta(seconds=1),NOW+timedelta(hours=2),NOW.replace(hour=18)):
            self.assertFalse(engine.tick(now))
        self.fetch.assert_not_called();self.notifier.send_card.assert_not_called()
        (self.root/'config/daily_brief.json').write_text('{"enabled":false}',encoding='utf-8')
        self.assertFalse(engine.tick(NOW));self.fetch.assert_not_called()

    def test_restart_and_multiple_ticks_do_not_duplicate(self):
        self.assertTrue(self.engine().tick(NOW))
        for minute in (1,5,30):self.assertFalse(self.engine().tick(NOW+timedelta(minutes=minute)))
        self.assertEqual(self.fetch.call_count,1);self.assertEqual(self.notifier.send_card.call_count,1)

    def test_timeout_retries_cached_payload_with_stable_uuid(self):
        self.notifier.send_card.side_effect=[TimeoutError('network'),None]
        self.assertFalse(self.engine().tick(NOW))
        first=self.notifier.send_card.call_args
        self.assertFalse(self.engine().tick(NOW+timedelta(seconds=30)))
        self.assertTrue(self.engine().tick(NOW+timedelta(minutes=1)))
        self.assertEqual(self.fetch.call_count,1)
        self.assertEqual(self.notifier.send_card.call_args,first)

    def test_data_failure_never_uses_old_report_and_recovers(self):
        (self.root/'out').mkdir();(self.root/'out/model_old.json').write_text('{"old":true}',encoding='utf-8')
        self.fetch.side_effect=[ValueError('private upstream text'),fixtures()]
        self.assertFalse(self.engine().tick(NOW));self.notifier.send_card.assert_not_called()
        self.assertTrue(self.engine().tick(NOW+timedelta(minutes=1)))
        self.assertNotIn('private upstream text',str(self.notifier.send_card.call_args))
        self.assertEqual((self.root/'out/model_old.json').read_text(),'{"old":true}')

    def test_repeated_failure_notifies_once_then_valid_report_can_follow(self):
        self.fetch.side_effect=ValueError('private detail')
        for minute in range(4):self.assertFalse(self.engine().tick(NOW+timedelta(minutes=minute)))
        self.assertEqual(self.notifier.send_card.call_count,1)
        self.assertIn('暂不可用',str(self.notifier.send_card.call_args))
        self.assertNotIn('private detail',str(self.notifier.send_card.call_args))
        self.fetch.side_effect=None;self.fetch.return_value=fixtures()
        self.assertTrue(self.engine().tick(NOW+timedelta(minutes=4)))
        self.assertEqual(self.notifier.send_card.call_count,2)

    def test_account_scope_validation_and_destination_allowlist(self):
        rows,meta=fixtures();meta['advertiser_id']='888';self.fetch.return_value=(rows,meta)
        self.assertFalse(self.engine().tick(NOW));self.notifier.send_card.assert_not_called()
        bad=dict(ENV,FEISHU_DAILY_BRIEF_CHAT_ID='oc_forbidden')
        with self.assertRaises(ValueError):self.engine(bad).tick(NOW)

    def test_no_silent_rerouting_existing_job_after_group_change(self):
        self.notifier.send_card.side_effect=TimeoutError()
        self.assertFalse(self.engine().tick(NOW))
        changed=dict(ENV,FEISHU_DAILY_BRIEF_CHAT_ID='oc_second')
        with self.assertRaises(ValueError):self.engine(changed).tick(NOW+timedelta(minutes=1))
        self.assertEqual(self.notifier.send_card.call_count,1)

    def test_shanghai_date_utc_year_boundary_and_late_catchup(self):
        utc=datetime(2027,1,1,2,30,tzinfo=timezone.utc)
        self.fetch.return_value=fixtures('2026-12-31')
        self.assertTrue(self.engine().tick(utc))
        self.assertEqual(self.fetch.call_args.args[1],'2026-12-31')
        self.assertEqual(self.fetch.call_args.kwargs['today'].isoformat(),'2027-01-01')
        with self.assertRaises(ValueError):self.engine().tick(NOW.replace(tzinfo=None))

    def test_zero_spend_unknown_profit_and_top_plan_limit(self):
        report={'audit':{'start':'2026-10-28','end':'2026-10-28','days':1},'source_api':{'advertiser_id':'999'},
                'totals':{'gmv':'100','ad_spend':'0','predicted_profit':None},
                'plans':[{'plan_id':str(i),'plan_name':'计划'+str(i),'gmv':'100','ad_spend':'0','predicted_profit':None} for i in range(5)]}
        rendered=d.render(report,max_plans=2);text=json.dumps(rendered,ensure_ascii=False)
        self.assertIn('未给出完整利润预测',text);self.assertIn('前2条',text)
        self.assertNotIn('计划2（',text);self.assertIn('零消耗时暂无',text)

    def test_daily_roles_are_separate_from_balance_alerts(self):
        cfg=json.loads((self.root/'config/rules.json').read_text(encoding='utf-8'))
        cfg['notify']['roles']['投手']=['ou_test']
        (self.root/'config/rules.json').write_text(json.dumps(cfg),encoding='utf-8')
        (self.root/'config/daily_brief.json').write_text('{"at_roles":["投手"]}',encoding='utf-8')
        self.assertTrue(self.engine().tick(NOW))
        self.assertIn('<at id="ou_test"></at>',str(self.notifier.send_card.call_args.args[0]))


if __name__=='__main__':unittest.main()
