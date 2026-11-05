import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import analysis_gate

A = '9000000000000099'
OTHER = '9000000000000004'
NOW = datetime(2026, 10, 29, 15, 0, tzinfo=timezone(timedelta(hours=8)))

class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assertIsNotNone(importlib.util.find_spec('agent.trading_context'), '统一上下文功能尚未实现')
        from agent import trading_context as tc
        self.tc = tc
        self.home = tc.initialize(self.root, A)

    def dump(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def report(self, account=A, end='2026-10-28', **extra):
        row = {'plan_id': '100', 'plan_name': '马甲', 'gmv': '320', 'ad_spend': '50',
               'backend_net': '200', 'comprehensive_cost': '50', 'predicted_profit': '999'}
        report = {'source_api': {'advertiser_id': account,'data_topic':analysis_gate.TOPICS['plan'],
                  'monetary_unit':'CNY','start':end,'end':end,'days':1,'fetched_at':NOW.isoformat()}, 'audit': {'start':end, 'end':end,'days':1},
                  'totals': {k:row[k] for k in ('gmv','ad_spend','backend_net','comprehensive_cost')},
                  'plans':[row], 'assumptions':{'commission_rate':'.05'}}
        report.update(extra)
        return report

    def snapshots(self, minutes=(60,30,0)):
        rows=[]
        for i, minutes_ago in enumerate(minutes):
            at = NOW-timedelta(minutes=minutes_ago)
            rows.append({'account':A,'kind':'plan','day':at.date().isoformat(), 'bucket':at.isoformat(),
              'fetched_at':at.isoformat(),'request_end':at.isoformat(), 'topic':analysis_gate.TOPICS['plan'],
              'scope':analysis_gate.SCOPES['plan'],'unit':'CNY','rows':[{'id':'100','name':'马甲',
              'gmv':str(100+i*100),'ad_spend':str(10+i*10),'backend_net':str(90+i*90),
              'comprehensive_cost':str(10+i*10),'order_count':str(1+i)}]})
        path=self.root/'data/qianchuan/monitor.db';path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE snapshots(account TEXT,kind TEXT,bucket TEXT,stamp TEXT,payload TEXT)')
            for row in rows:
                db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?)',(A,'plan',row['bucket'],row['fetched_at'],json.dumps(row)))
        return rows

    def build(self, **kw):
        return self.tc.build(self.root,A,now=NOW,**kw)

    def test_four_entries_and_empty_is_missing(self):
        p=self.build()
        self.assertEqual(set(p['entries']),{'business_rules','business_facts','knowledge_cases','execution_limits'})
        self.assertIsNone(p['entries']['business_facts']['current']['totals'])
        self.assertEqual(p['decision_mode'],'AD_PLAN_SIMULATION')
        self.assertIn('operation_history_missing',p['data_gaps'])
        self.assertTrue((self.home/'03_knowledge/operations').is_dir())
        self.assertTrue((self.home/'03_knowledge/materials').is_dir())

    def test_initialize_preserves_user_configuration(self):
        path=self.home/'04_limits/limits.json';path.write_text('{"my_setting":1}')
        self.tc.initialize(self.root,A)
        self.assertEqual(json.loads(path.read_text()),{'my_setting':1})

    def test_formulas_and_no_commission(self):
        from agent.context_finance import calculate, defaults
        x=calculate('320','50',defaults(A),None)
        self.assertEqual(x['payment_roi'],'6.4')
        self.assertEqual(x['contribution_margin'],'0.5566')
        self.assertIsNone(x['predicted_profit'])
        self.assertIsNone(x['breakeven_payment_roi'])
        self.assertEqual(x['commission_cost'],'0')

    def test_refund_scope_and_calculation(self):
        from agent.context_finance import calculate, defaults
        rules=defaults(A)
        rules['refund_assumptions']=[{'advertiser_id':A,'sku':'sku1','scene':'goods','valid_from':'2026-10-01',
            'valid_to':'2026-10-31','status':'CONFIRMED','rate':'.5','basis':'full_amount_refund_scenario',
            'evidence':'用户确认的本商品退款情景'}]
        match={'advertiser_id':A,'sku':'sku1','scene':'goods','start':'2026-10-28','end':'2026-10-28'}
        result=calculate('320','50',rules,match)
        self.assertEqual(Decimal(result['predicted_profit']),Decimal('39.0560'))
        self.assertAlmostEqual(float(result['breakeven_payment_roi']),1/(.5*.5566))
        match['sku']='sku2'
        self.assertIsNone(calculate('320','50',rules,match)['predicted_profit'])

    def test_zero_spend_undefined(self):
        from agent.context_finance import calculate, defaults
        self.assertIsNone(calculate('0','0',defaults(A),None)['payment_roi'])

    def test_invalid_finance_rejected(self):
        from agent.context_finance import calculate, defaults
        for x in ('NaN','Infinity','-1'):
            with self.subTest(x=x), self.assertRaises(ValueError): calculate(x,'1',defaults(A),None)

    def test_legacy_profit_and_other_account_excluded(self):
        self.dump('out/model_a.json',self.report())
        self.dump('out/model_b.json',self.report(OTHER))
        p=self.build();history=p['entries']['business_facts']['history']
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['calculations']['payment_roi'],'6.4')
        self.assertIsNone(history[0]['calculations']['predicted_profit'])
        self.assertNotIn('999',json.dumps(p))
        self.assertNotIn(OTHER,json.dumps(p))

    def test_future_report_and_bad_totals_excluded(self):
        self.dump('out/model_future.json',self.report(end='2026-10-30'))
        bad=self.report();bad['totals']['gmv']='321'
        self.dump('out/model_bad.json',bad)
        self.assertEqual(self.build()['entries']['business_facts']['history'],[])

    def test_duplicate_plan_rejected(self):
        bad=self.report();bad['plans']*=2
        self.dump('out/model_dup.json',bad)
        self.assertEqual(self.build()['entries']['business_facts']['history'],[])

    def test_full_history_plans_not_top20(self):
        r=self.report();r['plans']=[dict(r['plans'][0],plan_id=str(i)) for i in range(30)]
        r['totals']={k:str(float(v)*30) for k,v in r['totals'].items()}
        self.dump('out/model_many.json',r)
        self.assertEqual(len(self.build()['entries']['business_facts']['history'][0]['plans']),30)

    def test_current_totals_and_gate(self):
        self.snapshots()
        x=self.build()['entries']['business_facts']['current']
        self.assertEqual(x['totals']['gmv'],'300')
        self.assertEqual(x['analysis_admission']['state'],'ready')
        self.assertIsNone(x['calculations']['predicted_profit'])

    def test_gaps_do_not_erase_cumulative(self):
        self.snapshots((90,30,0))
        x=self.build()['entries']['business_facts']['current']
        self.assertEqual(x['totals']['gmv'],'300')
        self.assertNotEqual(x['analysis_admission']['state'],'ready')

    def test_readonly_source_db_unchanged(self):
        self.snapshots();path=self.root/'data/qianchuan/monitor.db'
        before=path.read_bytes();self.build();self.assertEqual(path.read_bytes(),before)
        self.assertFalse((self.root/'data/qianchuan/hourly.db').exists())

    def test_material_is_unreviewed_and_no_secret_export(self):
        (self.home/'03_knowledge/materials/测试.md').write_text('预算策略：先核对ROI',encoding='utf-8')
        (self.home/'03_knowledge/materials/key.txt').write_text('api_key=secret-value',encoding='utf-8')
        p=self.build(question='预算ROI')
        self.assertTrue(p['entries']['knowledge_cases']['snippets'])
        self.assertTrue(all(x['status']=='REVIEW_REQUIRED' for x in p['entries']['knowledge_cases']['snippets']))
        self.assertNotIn('secret-value',json.dumps(p))
        self.assertIn('sensitive_source_excluded',p['data_gaps'])

    def test_account_mismatch_in_rules_blocks_build(self):
        path=self.home/'01_rules/rules.json';r=json.loads(path.read_text());r['advertiser_id']=OTHER
        path.write_text(json.dumps(r))
        with self.assertRaises(ValueError):self.build()

    def test_limits_never_authorize_writes(self):
        p=self.build()
        self.assertFalse(p['execution']['write_authorized'])
        self.assertIn('platform_roi_mapping_missing',p['data_gaps'])
        self.assertIn('execution_limits_unconfirmed',p['data_gaps'])

    def test_atomic_export_four_entries_and_manifest(self):
        directory=self.tc.export(self.root,A,now=NOW)
        manifest=json.loads((directory/'manifest.json').read_text())
        self.assertEqual(len(manifest['files']),6)
        self.assertTrue((directory/'context.json').exists())
        self.assertTrue((directory/'GROK_CONTEXT_PROMPT.md').exists())
        latest=json.loads((self.home/'latest.json').read_text())
        self.assertEqual(latest['bundle'],directory.name)

    def test_invalid_account_no_path_escape(self):
        for value in ('../x','1,2','账户',''):
            with self.subTest(value=value),self.assertRaises(ValueError):self.tc.initialize(self.root,value)

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):self.tc.build(self.root,A,now=NOW.replace(tzinfo=None))

    def test_no_external_calls(self):
        from unittest.mock import patch
        with patch('urllib.request.urlopen',side_effect=AssertionError('禁止联网')):
            self.build()

    def test_hourly_coverage_separate_from_cumulative(self):
        path=self.root/'data/qianchuan/hourly.db';path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE hours(account TEXT,start TEXT,payload TEXT)')
            item={'advertiser_id':A,'start':'2026-10-29T00:00:00+08:00','end':'2026-10-29T01:00:00+08:00',
                  'monetary_unit':'CNY','data_topic':analysis_gate.TOPICS['plan'],
                  'fetched_at':'2026-10-29T01:01:00+08:00','rows':[{'plan_id':'100','plan_name':'马甲',
                  'gmv':'100','ad_spend':'10','backend_net':'90','comprehensive_cost':'10'}]}
            db.execute('INSERT INTO hours VALUES(?,?,?)',(A,item['start'],json.dumps(item)))
        self.snapshots()
        facts=self.build()['entries']['business_facts']
        self.assertEqual(facts['current']['totals']['gmv'],'300')
        self.assertEqual(facts['hourly']['totals']['gmv'],'100')
        self.assertEqual(len(facts['hourly']['missing_hours']),14)

    def test_existing_budget_snapshot_is_read(self):
        self.dump('data/qianchuan/budgets/budgets_a.json',{'advertiser_id':A,'kind':'current_budget',
                  'fetched_at':NOW.isoformat(),'plans':[{'plan_id':'100','amount':'300','mode':'BUDGET_MODE_DAY',
                  'status':'ok','fetched_at':NOW.isoformat()}]})
        b=self.build()['entries']['business_facts']['budgets']
        self.assertEqual(b['plans'][0]['amount'],'300')

    def test_stale_snapshots_dont_become_fresh_on_export(self):
        self.snapshots((120,90,60))
        x=self.build()['entries']['business_facts']['current']
        self.assertIn('stale',x['analysis_admission']['reasons'])
        self.assertEqual(x['totals']['gmv'],'300')

    def test_duplicate_refund_entries_do_not_select_arbitrarily(self):
        from agent.context_finance import defaults,calculate
        r=defaults(A);item={'advertiser_id':A,'sku':'s','scene':'goods','valid_from':'2026-10-01',
            'valid_to':'2026-10-31','status':'CONFIRMED','rate':'.5','basis':'full_amount_refund_scenario','evidence':'x'}
        r['refund_assumptions']=[item,item]
        self.assertIsNone(calculate('100','10',r,dict(item,start='2026-10-28',end='2026-10-28'))['refund_rate'])

    def test_unknown_report_scope_not_assumed_chuanfang(self):
        bad=self.report();bad['source_api']['data_topic']='OTHER_TOPIC'
        self.dump('out/model_wrong_topic.json',bad)
        self.assertEqual(self.build()['entries']['business_facts']['history'],[])

    def test_latest_pointer_preserved_on_failed_export(self):
        self.tc.export(self.root,A,now=NOW);pointer=(self.home/'latest.json').read_bytes()
        path=self.home/'01_rules/rules.json';r=json.loads(path.read_text());r['advertiser_id']=OTHER
        path.write_text(json.dumps(r))
        with self.assertRaises(ValueError):self.tc.export(self.root,A,now=NOW)
        self.assertEqual((self.home/'latest.json').read_bytes(),pointer)

    def test_native_card_archives_are_not_report_inputs(self):
        self.dump('out/model_a_cards.json',[{'header':{'title':'卡片'}}])
        self.dump('out/model_a.json',self.report())
        self.assertEqual(len(self.build()['entries']['business_facts']['history']),1)

    def test_export_ready_snapshots_serializes_decimal_evidence(self):
        self.snapshots()
        directory=self.tc.export(self.root,A,now=NOW)
        p=json.loads((directory/'context.json').read_text(encoding='utf-8'))
        self.assertEqual(p['entries']['business_facts']['current']['analysis_admission']['state'],'ready')

    def test_future_tolerance_is_not_used_for_context_facts(self):
        rows=self.snapshots()
        last=rows[-1];future=(NOW+timedelta(minutes=3)).isoformat()
        last.update(request_end=future,fetched_at=future)
        with closing(sqlite3.connect(self.root/'data/qianchuan/monitor.db')) as db,db:
            db.execute('UPDATE snapshots SET payload=? WHERE bucket=?',(json.dumps(last),NOW.isoformat()))
        x=self.build()
        self.assertNotEqual(x['entries']['business_facts']['current'].get('cutoff'),future)
        self.assertIn('future_snapshot_excluded',x['data_gaps'])

    def test_future_operation_report_excluded(self):
        report={'schema_version':1,'advertiser_id':A,'report_date':'2026-10-28',
            'generated_at':(NOW+timedelta(minutes=3)).isoformat(),'complete':True,
            'source':'Grok','summary':'无操作，自报未独立核验','actions':[]}
        self.dump('data/grok_operations/'+A+'/2026-10-28.json',report)
        p=self.build()
        self.assertEqual(p['entries']['knowledge_cases']['operations'],[])
        self.assertIn('operation_record_rejected',p['data_gaps'])

    def test_export_partial_evidence_preserves_unavailable_metrics(self):
        rows=self.snapshots();rows[-1]['rows'][0]['order_count']='NaN'
        with closing(sqlite3.connect(self.root/'data/qianchuan/monitor.db')) as db,db:
            db.execute('UPDATE snapshots SET payload=? WHERE bucket=?',(json.dumps(rows[-1]),NOW.isoformat()))
        directory=self.tc.export(self.root,A,now=NOW)
        p=json.loads((directory/'context.json').read_text(encoding='utf-8'))
        self.assertEqual(p['entries']['business_facts']['current']['analysis_admission']['state'],'partial')

if __name__=='__main__':unittest.main()
