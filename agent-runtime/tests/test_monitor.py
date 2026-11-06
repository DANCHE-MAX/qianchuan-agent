"""No-network regression for snapshots, anomalies, cash rates and durable sends."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from decimal import Decimal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import monitor as m,settings,notify,hourly,listener

NOW=datetime(2026,10,28,20,0,tzinfo=hourly.CHINA)
ENV={'QC_ADVERTISER_IDS':'999','QC_ACCESS_TOKEN':'secret','FEISHU_CHAT_ID':'oc_test','KNOWLEDGE_ENABLED':'true'}


def snap(stamp,spend='0',gmv='0',orders='0',kind='plan',account='999'):
    return {'account':account,'kind':kind,'bucket':m.bucket(stamp).isoformat(),'day':stamp.date().isoformat(),
        'fetched_at':stamp.isoformat(),'request_end':stamp.isoformat(),'topic':m.TOPICS[kind][0],'unit':'CNY','scope':m.gate.SCOPES[kind],'rows':[{'id':'123','name':'测试马甲',
        'ad_spend':str(spend),'gmv':str(gmv),'order_count':str(orders),'backend_net':str(gmv),'comprehensive_cost':str(spend)}]}


def balance(stamp,amount,frozen='0',account='999'):
    return {'advertiser_id':account,'fetched_at':stamp.isoformat(),'amounts_yuan':{
        'account_total':str(Decimal(str(amount))+Decimal(frozen)),'account_valid':str(amount),'account_frozen':frozen}}


def balance_window(end,last):
    return [balance(end-timedelta(minutes=30*(8-i)),Decimal(str(last))+Decimal(100)*(8-i)) for i in range(9)]


class Tests(unittest.TestCase):
    def setUp(self):
        self.cfg=settings.load_config();self.mc=self.cfg['monitoring']
        self.model=copy.deepcopy(self.cfg['model']);self.model['plans']={'123':{'refund_band':'overall'}}

    def test_snapshot_pagination_units_empty_and_fail_closed(self):
        def row(pid):
            return {'dimensions':{'ad_id':{'Value':pid},'ad_name':{'Value':'马甲'},'stat_time_day':{'ValueStr':'2026-10-28'}},
                'metrics':{field:{'Value':'1' if key=='order_count' else '12.34'} for key,field in m.METRICS.items()}}
        def page(rows,n=1,total=2,pages=2):return {'code':0,'data':{'rows':rows,'page_info':{'page':n,'total_number':total,'total_page':pages}}}
        request=Mock(side_effect=[page([row('123')]),page([row('456')],2)])
        value=m.fetch_snapshot(ENV,'plan',NOW,request=request)
        self.assertEqual(value['rows'][0]['ad_spend'],'12.34')
        self.assertEqual(len(value['raw_pages']),2)
        self.assertEqual(request.call_args.args[0]['start_time'],'2026-10-28 00:00:00')
        with self.assertRaises(ValueError):m.fetch_snapshot(ENV,'plan',NOW,request=Mock(return_value=page([row('123'),row('123')],pages=1)))
        empty=m.fetch_snapshot(ENV,'material',NOW,request=Mock(return_value=page([],total=0,pages=0)))
        self.assertEqual(empty['rows'],[])

    def test_anomaly_minimums_revision_gaps_and_cross_day(self):
        series=[snap(NOW-timedelta(minutes=60),'0'),snap(NOW-timedelta(minutes=30),'20'),snap(NOW,'100')]
        hits=m.anomalies(series,self.mc,self.model,now=NOW)
        self.assertEqual(hits[0]['spend'],'80')
        series[-1]['rows'][0]['ad_spend']='40'
        self.assertFalse(m.anomalies(series,self.mc,self.model,now=NOW))
        series=[snap(NOW-timedelta(minutes=60),'0','0','0'),snap(NOW-timedelta(minutes=30),'20','100','1'),snap(NOW,'40','1000','7')]
        self.assertIn('快速增加',str(m.anomalies(series,self.mc,self.model,now=NOW)))
        series[-1]['rows'][0]['gmv']='50'
        self.assertFalse(m.anomalies(series,self.mc,self.model,now=NOW))
        self.assertIsNone(m.changes(snap(NOW-timedelta(days=1)),snap(NOW)))
        self.assertIsNone(m.changes(snap(NOW-timedelta(hours=2)),snap(NOW)))
        self.assertIsNone(m.changes(snap(NOW-timedelta(minutes=30),account='888'),snap(NOW)))

    def test_balance_rate_and_two_hour_runway(self):
        samples=balance_window(NOW,'3700');rate=m.balance_rate(samples,samples[-1])
        self.assertEqual(Decimal(rate['rate']),Decimal('200'))
        rendered=str(m.balance_notice(samples[-1],rate))
        self.assertIn('还能用约18.50小时',rendered)
        self.assertIn('暂不需要充值',rendered);self.assertNotIn('参考线约剩',rendered)
        self.assertNotIn('次日10:00',rendered)
        self.assertIsNone(m.balance_rate([],samples[-1])['rate'])
        bad=copy.deepcopy(samples);bad[-3]['amounts_yuan']['account_total']='9999'
        self.assertIsNone(m.balance_rate(bad,bad[-1])['rate'])
        self.assertIsNone(m.balance_rate([samples[0],samples[-1]],samples[-1])['rate'])
        frozen=copy.deepcopy(samples);frozen[-3]['amounts_yuan']['account_frozen']='10'
        self.assertIsNone(m.balance_rate(frozen,frozen[-1])['rate'])
        unknown=m.balance_notice(samples[-1],{'rate':None,'reason':'缺少四小时前基准'})
        self.assertIn('无法估算',str(unknown))

    def make_root(self,tmp):
        root=Path(tmp);(root/'config').mkdir()
        (root/'config/rules.json').write_text(json.dumps(self.cfg),encoding='utf-8')
        return root

    def test_scheduled_notices_restart_retry_idempotency_and_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.make_root(tmp);store=m.Store(root);samples=balance_window(NOW,'3700')
            for sample in samples[:-1]:store.save_balance(sample)
            nf=Mock();nf.send_card.side_effect=[TimeoutError(),None]
            factory=Mock(return_value=nf)
            def fetch(env,kind,now):return snap(now,kind=kind)
            with patch.object(m,'fetch_snapshot',side_effect=fetch),patch.object(m.account_balance,'fetch',return_value=samples[-1]) as balance_api:
                with self.assertRaises(TimeoutError):m.Engine(root,ENV,factory).tick(NOW)
                first=nf.send_card.call_args.kwargs['request_uuid']
                m.Engine(root,ENV,factory).tick(NOW+timedelta(minutes=1))
                self.assertEqual(nf.send_card.call_count,2)
                self.assertEqual(nf.send_card.call_args.kwargs['request_uuid'],first)
                self.assertEqual(balance_api.call_count,1)
                self.assertEqual(store.pending('999'),[])
                m.Engine(root,ENV,factory).tick(NOW+timedelta(minutes=2))
                self.assertEqual(nf.send_card.call_count,2)
            factory.assert_called_with('oc_test')
            self.assertEqual(store.pending('888'),[])

    def test_six_scheduled_checks_are_four_hour_rates_with_runway(self):
        for hour in (0,4,8,12,16,20):
            with self.subTest(hour=hour), tempfile.TemporaryDirectory() as tmp:
                root=self.make_root(tmp);now=NOW.replace(hour=hour);samples=balance_window(now,'5000')
                for sample in samples[:-1]:m.Store(root).save_balance(sample)
                nf=Mock()
                with patch.object(m,'fetch_snapshot',side_effect=lambda env,kind,stamp:snap(stamp,kind=kind)),patch.object(m.account_balance,'fetch',return_value=samples[-1]):
                    m.Engine(root,ENV,lambda chat:nf).tick(now)
                text=str(nf.send_card.call_args.args[0])
                self.assertIn('近两小时余额净下降速度估算',text)
                self.assertIn('还能用约25.00小时',text)
                self.assertNotIn('次日10:00余额约',text)

    def test_anomaly_reads_balance_immediately_and_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.make_root(tmp);store=m.Store(root);now=NOW.replace(hour=15,minute=30)
            store.save_snapshot(snap(now-timedelta(minutes=60),'0'))
            store.save_snapshot(snap(now-timedelta(minutes=30),'20'))
            nf=Mock();kb=Mock();kb.ask.return_value='异常摘要'
            def fetch(env,kind,stamp):
                value=snap(stamp,'100',kind=kind)
                if kind=='material':value['rows']=[]
                return value
            with patch.object(m,'fetch_snapshot',side_effect=fetch),patch.object(m.account_balance,'fetch',return_value=balance(now,'1000')) as api,patch.object(m,'Knowledge',return_value=kb):
                m.Engine(root,ENV,lambda chat:nf).tick(now)
                self.assertEqual(api.call_count,1)
                self.assertEqual(nf.send_card.call_count,1)
                m.Engine(root,ENV,lambda chat:nf).tick(now+timedelta(minutes=1))
                self.assertEqual(nf.send_card.call_count,1)
            self.assertIn('余额',kb.ask.call_args.kwargs['report_context'])

    def test_today_prefers_new_snapshot_and_does_not_mix_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=m.Store(root)
            store.save_snapshot(snap(NOW,'100','1000','4'))
            store.save_snapshot(snap(NOW,'99999','999999',account='888'))
            context=json.loads(hourly.today_context(root,ENV,'目前ROI',now=NOW))
            self.assertEqual(context['资料类型'],'今日半小时采集')
            self.assertEqual(context['整体']['payment_roi'],'10')
            self.assertNotIn('999999',str(context))
            kb=Mock(last_context=None);kb.ask.return_value='ROI 10'
            with patch.object(hourly,'now_china',return_value=NOW):
                reply=listener.answer('目前ROI',root/'out',ENV,kb)
            self.assertIn('截至20:00',reply)
            self.assertIn('整体支付ROI10.00',reply)

    def test_alert_mentions_are_native_deduplicated_and_category_specific(self):
        cfg=copy.deepcopy(self.cfg)
        cfg['notify']['roles']={'投手':['ou_trader','ou_trader','bad<id>','all'],'老板':['owner@example.com']}
        cfg['monitoring']['at_roles']={'anomaly':['投手'],'balance_alert':['老板'],'balance_check':[]}  # Synthetic policy, independent of local production roles.
        abnormal=m.add_mentions(m.card('异常',[]),cfg,'anomaly')
        content=abnormal['elements'][-1]['text']
        self.assertEqual(content['tag'],'lark_md')
        self.assertEqual(content['content'].count('ou_trader'),1)
        self.assertNotIn('bad<id>',content['content'])
        self.assertNotIn('id="all"',content['content'])
        self.assertIn('<at email="owner@example.com"></at>',str(m.add_mentions(m.card('充值',[]),cfg,'balance_alert')))
        self.assertEqual(m.add_mentions(m.card('正常',[]),cfg,'balance_check'),m.card('正常',[]))

    def test_proactive_uuid_reaches_feishu_request(self):
        nf=notify.FeishuAppNotifier('app','secret','oc_test');nf._tenant_token=lambda:'token'
        nf._request=Mock(return_value={'code':0})
        nf.send_card({'elements':[]},request_uuid='stable-id')
        self.assertEqual(nf._request.call_args.args[2]['uuid'],'stable-id')


if __name__=='__main__':unittest.main()

# analysis-admission-v26

# current-brief-v27
