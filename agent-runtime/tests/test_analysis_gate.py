"""No-network fault scenarios for admission, per-metric rules and durable status changes."""
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal
import copy
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import analysis_gate as g, monitor as m, hourly as h, settings, listener

NOW=datetime(2026,10,29,15,30,tzinfo=h.CHINA)
ENV={'QC_ADVERTISER_IDS':'999','QC_ACCESS_TOKEN':'sandbox','FEISHU_CHAT_ID':'oc_test','KNOWLEDGE_ENABLED':'true'}


def row(pid='123',spend='0',gmv='0',orders='0'):
    return {'id':pid,'name':'测试计划'+pid,'ad_spend':spend,'gmv':gmv,'order_count':orders,
            'backend_net':gmv,'comprehensive_cost':spend}


def snapshot(at,rows=None,kind='plan'):
    return {'account':'999','kind':kind,'day':at.date().isoformat(),'bucket':m.bucket(at).isoformat(),
            'fetched_at':at.isoformat(),'request_end':at.isoformat(),'topic':g.TOPICS[kind],
            'unit':'CNY','scope':g.SCOPES[kind],'rows':[row()] if rows is None else rows}


def series():
    return [snapshot(NOW-timedelta(minutes=60),[row()]),
            snapshot(NOW-timedelta(minutes=30),[row(spend='20')]),
            snapshot(NOW,[row(spend='100')])]


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'config').mkdir()
        cfg=settings.load_config();cfg['monitoring']['enabled']=True
        (self.root/'config/rules.json').write_text(json.dumps(cfg),encoding='utf-8')
        self.cfg=cfg;self.store=m.Store(self.root)
        self.net=patch('urllib.request.urlopen',side_effect=AssertionError('No real network'))
        self.net.start();self.addCleanup(self.net.stop)

    def assess(self,values,now=NOW):return g.assess(values,'999','plan',now)

    def hits(self,values,now=NOW):return m.anomalies(values,self.cfg['monitoring'],self.cfg['model'],now=now,account='999',kind='plan')

    def test_valid_three_points_produce_existing_no_sale_rule(self):
        values=series();self.assertEqual(self.assess(values)['state'],'ready')
        findings=self.hits(values);self.assertEqual(findings[0]['spend'],'80')
        self.assertIn('无成交',str(findings))

    def test_one_or_two_snapshots_explicitly_unavailable(self):
        for count in (1,2):
            values=series()[-count:];value=self.assess(values)
            self.assertEqual(value['state'],'waiting');self.assertTrue(value['cumulative_valid'])
            self.assertEqual(self.hits(values),[]);self.assertIn('暂无法判断',g.describe(value))

    def test_missing_bucket_blocks_comparison(self):
        values=series();values[0]=snapshot(NOW-timedelta(minutes=90))
        self.assertIn('gap',self.assess(values)['reasons']);self.assertFalse(self.hits(values))

    def test_same_bucket_is_not_three_independent_samples(self):
        values=series();values[1]=copy.deepcopy(values[-1])
        self.assertFalse(self.hits(values));self.assertIn('gap',self.assess(values)['reasons'])

    def test_25_and_40_minute_edges_allowed_and_outside_rejected(self):
        for seconds,accepted in ((25*60,True),(40*60,True),(25*60-1,False),(40*60+1,False)):
            values=series();base=NOW.replace(hour=14,minute=10,second=0)
            times=[base,base+timedelta(seconds=seconds),base+timedelta(seconds=seconds,minutes=30)]
            values=[snapshot(at,[row(spend=str(i*100))]) for i,at in enumerate(times)]
            result=self.assess(values,now=times[-1])
            self.assertEqual(result['state']=='ready',accepted,(seconds,result))

    def test_stale_points_not_used_as_current(self):
        values=series();result=self.assess(values,NOW+timedelta(minutes=46))
        self.assertIn('stale',result['reasons']);self.assertTrue(result['cumulative_valid'])
        self.assertFalse(self.hits(values,NOW+timedelta(minutes=46)))

    def test_account_kind_scope_topic_unit_or_day_mismatch_blocks(self):
        for key,value in (('account','888'),('kind','material'),('scope','其他范围'),('topic','OTHER'),('unit','CENT'),('day','2026-10-28')):
            values=series();values[1][key]=value
            self.assertEqual(self.assess(values)['state'],'blocked',key);self.assertFalse(self.hits(values))

    def test_cross_midnight_is_not_same_day_acceleration(self):
        at=NOW.replace(hour=0,minute=30)
        values=[snapshot(at-timedelta(hours=1)),snapshot(at-timedelta(minutes=30)),snapshot(at,[row(spend='100')])]
        self.assertFalse(self.hits(values,at));self.assertIn('metadata',self.assess(values,at)['reasons'])

    def test_future_naive_or_invalid_times_block(self):
        for key,value in (('request_end',(NOW+timedelta(minutes=10)).isoformat()),
                          ('fetched_at',NOW.replace(tzinfo=None).isoformat()),('bucket','invalid')):
            values=series();values[-1][key]=value
            self.assertFalse(self.hits(values));self.assertFalse(self.assess(values)['cumulative_valid'])

    def test_duplicate_or_invalid_id_blocks_snapshot(self):
        values=series();values[-1]['rows']*=2
        self.assertIn('identity',self.assess(values)['reasons'])
        values=series();values[1]['rows'][0]['id']='bad'
        self.assertFalse(self.hits(values))

    def test_new_entity_history_not_filled_with_zero(self):
        values=series();values[0]['rows']=[]
        result=self.assess(values)
        self.assertEqual(result['entities']['123']['metrics']['ad_spend'],'presence')
        self.assertFalse(self.hits(values));self.assertEqual(g.interval(values[0],values[1]),{})

    def test_disappearing_and_reappearing_entity_not_compared(self):
        for index in (1,2):
            values=series();values[index]['rows']=[]
            self.assertFalse(self.hits(values));self.assertIn('presence',self.assess(values)['reasons'])

    def test_spend_revision_stops_spend_rules(self):
        values=series();values[1]['rows'][0]['ad_spend']='200'
        result=self.assess(values);self.assertIn('revision',result['reasons']);self.assertFalse(self.hits(values))

    def test_invalid_order_count_does_not_disable_valid_no_sale_check(self):
        values=series();values[-1]['rows'][0]['order_count']='NaN'
        findings=self.hits(values);self.assertTrue(findings)
        self.assertIsNone(findings[0]['orders']);self.assertIn('订单未核验',g.finding_text(findings[0]))

    def test_auxiliary_net_revision_does_not_disable_spend_gmv(self):
        values=series();values[0]['rows'][0]['backend_net']='10'
        self.assertEqual(self.assess(values)['entities']['123']['metrics']['backend_net'],'revision')
        self.assertTrue(self.hits(values))

    def test_missing_or_bad_gmv_disables_roi_rules(self):
        for value in (None,'NaN','Infinity','-1','abc'):
            values=series()
            if value is None:values[-1]['rows'][0].pop('gmv')
            else:values[-1]['rows'][0]['gmv']=value
            self.assertFalse(self.hits(values));self.assertFalse(self.assess(values)['cumulative_valid'])

    def test_fractional_orders_do_not_support_sales_burst(self):
        values=series();values[1]['rows'][0].update(gmv='100',order_count='1')
        values[-1]['rows'][0].update(gmv='1000',order_count='7.5')
        self.assertNotIn('快速增加',str(self.hits(values)))

    def test_sales_rule_survives_invalid_spend_without_fabricated_roi(self):
        values=series();values[1]['rows'][0].update(gmv='100',order_count='1')
        values[-1]['rows'][0].update(gmv='1000',order_count='7',ad_spend='NaN')
        findings=self.hits(values);self.assertIn('快速增加',str(findings))
        self.assertIsNone(findings[0]['spend']);self.assertIsNone(findings[0]['payment_roi'])
        self.assertIn('消耗未核验',g.finding_text(findings[0]))

    def test_valid_entities_not_blocked_by_new_entity(self):
        values=series();values[-1]['rows'].append(row('456','1000','5000','20'))
        findings=self.hits(values);self.assertEqual([f['id'] for f in findings],['123'])

    def test_true_zero_history_allows_growth(self):
        values=series();values[-1]['rows'][0].update(gmv='1000',order_count='6')
        self.assertIn('快速增加',str(self.hits(values)))

    def test_invalid_past_snapshot_does_not_destroy_valid_latest_cumulative(self):
        values=series();values[0]['topic']='BAD'
        for value in values:self.store.save_snapshot(value)
        info=json.loads(m.today_context(self.root,ENV,'目前ROI',NOW))
        self.assertEqual(info['整体']['ad_spend'],'100');self.assertEqual(info['异常分析准入']['state'],'blocked')
        self.assertIsNone(info['最近区间观测增量'])

    def test_current_context_keeps_totals_but_explicitly_says_insufficient(self):
        self.store.save_snapshot(series()[-1])
        info=json.loads(m.today_context(self.root,ENV,'目前消耗',NOW))
        self.assertEqual(info['整体']['ad_spend'],'100');self.assertIn('暂无法判断',info['分析限制'])
        self.assertIsNone(info['最近区间观测增量'])

    def test_context_rejects_invalid_latest_cumulative(self):
        value=series()[-1];value['rows'][0]['gmv']='NaN';self.store.save_snapshot(value)
        with self.assertRaises(ValueError):m.today_context(self.root,ENV,'目前ROI',NOW)

    def test_empty_is_not_reported_as_no_anomalies(self):
        result=self.assess([snapshot(NOW,[])])
        self.assertEqual(result['state'],'empty');self.assertIn('不能据此认定无风险',g.describe(result))

    def test_status_persistence_only_transition_not_counts(self):
        waiting=self.assess(series()[-1:]);g.record(self.store,waiting,NOW)
        self.assertEqual(len(self.store.pending('999')),1)
        g.record(m.Store(self.root),waiting,NOW+timedelta(minutes=1))
        self.assertEqual(len(self.store.pending('999')),1)
        partial=self.assess(series());partial['state']='partial';partial['reasons']=['presence']
        g.record(self.store,partial,NOW+timedelta(minutes=2));self.assertEqual(len(self.store.pending('999')),1)
        partial['counts']['limited']=100;g.record(self.store,partial,NOW+timedelta(minutes=3))
        self.assertEqual(len(self.store.pending('999')),1)

    def test_first_ready_and_empty_are_silent(self):
        g.record(self.store,self.assess(series()),NOW)
        g.record(self.store,self.assess([snapshot(NOW,[])]),NOW+timedelta(minutes=1))
        self.assertEqual(self.store.pending('999'),[])

    def test_ready_transition_means_evaluable_not_no_anomaly(self):
        waiting=self.assess(series()[-1:]);g.record(self.store,waiting,NOW)
        for key,_,_ in self.store.pending('999'):self.store.mark_sent(key)
        g.record(self.store,self.assess(series()),NOW+timedelta(minutes=1))
        payload=self.store.pending('999')[0][1]
        self.assertIn('条件已满足',payload);self.assertIn('不代表没有异常',payload)

    def test_entities_metric_limits_present_in_llm_context(self):
        values=series();values[-1]['rows'][0]['backend_net']='0';values[1]['rows'][0]['backend_net']='10'
        for value in values:self.store.save_snapshot(value)
        info=json.loads(m.today_context(self.root,ENV,'目前计划',NOW))
        self.assertEqual(info['逐计划分析资格'][0]['metrics']['backend_net'],'revision')

    def test_insufficient_notice_is_native_card_without_at(self):
        g.record(self.store,self.assess(series()[-1:]),NOW)
        payload=json.loads(self.store.pending('999')[0][1]);self.assertEqual(payload['header']['title']['tag'],'plain_text')
        self.assertNotIn('<at ',str(payload))

    def test_fetch_failure_state_notice_without_fake_trend(self):
        nf=Mock()
        def fetch(env,kind,now):
            if kind=='plan':raise TimeoutError('secret upstream')
            return snapshot(now,[],kind)
        value={'advertiser_id':'999','fetched_at':NOW.isoformat(),'amounts_yuan':{'account_total':'1000','account_valid':'1000','account_frozen':'0'}}
        with patch.object(m,'fetch_snapshot',side_effect=fetch),patch.object(m.account_balance,'fetch',return_value=value):
            with self.assertRaises(RuntimeError):m.Engine(self.root,ENV,lambda chat:nf).tick(NOW)
            self.assertEqual(nf.send_card.call_count,1)
            self.assertIn('采集失败',str(nf.send_card.call_args));self.assertNotIn('secret upstream',str(nf.send_card.call_args))

    def test_zero_activity_first_warmup_is_silent(self):
        value=self.assess([snapshot(NOW,[row()])]);self.assertEqual(value['state'],'waiting')
        g.record(self.store,value,NOW);self.assertEqual(self.store.pending('999'),[])

    def test_null_old_snapshot_is_blocked_not_crash(self):
        values=series();values[0]=None
        self.assertEqual(self.assess(values)['state'],'blocked');self.assertFalse(self.hits(values))

    def test_missing_past_metric_preserves_latest_totals(self):
        values=series();values[0]['rows'][0].pop('gmv')
        for value in values:self.store.save_snapshot(value)
        info=json.loads(m.today_context(self.root,ENV,'目前消耗',NOW))
        self.assertEqual(info['整体']['ad_spend'],'100')
        self.assertEqual(info['逐计划分析资格'][0]['metrics']['gmv'],'missing')

    def test_custom_stale_threshold_matches_quality_summary(self):
        (self.root/'config/health.json').write_text('{"snapshot_stale_minutes":30}',encoding='utf-8')
        for value in series():self.store.save_snapshot(value)
        info=json.loads(m.today_context(self.root,ENV,'目前',NOW+timedelta(minutes=31)))
        self.assertEqual(info['异常分析准入']['reasons'],['stale'])
        self.assertEqual(info['数据完整性检查']['components']['snapshot'],'stale')

    def test_status_send_timeout_retry_reuses_uuid(self):
        nf=Mock();nf.send_card.side_effect=[TimeoutError(),None]
        balance={'advertiser_id':'999','fetched_at':NOW.isoformat(),'amounts_yuan':{'account_total':'1000','account_valid':'1000','account_frozen':'0'}}
        def fetch(env,kind,at):return series()[-1] if kind=='plan' else snapshot(at,[],kind)
        with patch.object(m,'fetch_snapshot',side_effect=fetch),patch.object(m.account_balance,'fetch',return_value=balance):
            with self.assertRaises(TimeoutError):m.Engine(self.root,ENV,lambda chat:nf).tick(NOW)
            first=nf.send_card.call_args.kwargs['request_uuid']
            m.Engine(self.root,ENV,lambda chat:nf).tick(NOW+timedelta(minutes=1))
        self.assertEqual(nf.send_card.call_count,2);self.assertEqual(nf.send_card.call_args.kwargs['request_uuid'],first)

    def test_llm_false_all_clear_is_replaced_with_grounded_totals(self):
        self.store.save_snapshot(series()[-1]);info=json.loads(m.today_context(self.root,ENV,'目前',NOW))
        text=g.safe_answer('目前没有异常，一切正常。',info)
        self.assertIn('消耗100.00元',text);self.assertIn('暂不能判断',text);self.assertNotIn('一切正常',text)

    def test_cautious_llm_text_is_preserved(self):
        self.store.save_snapshot(series()[-1]);info=json.loads(m.today_context(self.root,ENV,'目前',NOW))
        text='样本不足不代表没有异常，累计消耗100元。'
        self.assertEqual(g.safe_answer(text,info),text)

    def test_legacy_pending_anomaly_is_retired(self):
        self.store.enqueue('anomaly:999:plan:123:old','999',NOW,m.card('旧准入告警',[]))
        g.prepare_store(self.store,'999');self.assertEqual(self.store.pending('999'),[])

    def test_pending_anomaly_requires_matching_metric_evidence(self):
        g.prepare_store(self.store,'999');values=series();findings=self.hits(values)
        g.bind(self.store,'proof','999',findings[0])
        self.assertTrue(g.allow_pending(self.store,'proof','999',{'plan':self.assess(values)}))
        self.assertFalse(g.allow_pending(self.store,'proof','888',{'plan':self.assess(values)}))
        limited=self.assess(values);limited['entities']['123']['metrics']['gmv']='revision'
        self.assertFalse(g.allow_pending(self.store,'proof','999',{'plan':limited}))
        self.assertFalse(g.allow_pending(self.store,'proof','999',{'plan':g.failed('999','plan',NOW)}))


if __name__=='__main__':unittest.main()
