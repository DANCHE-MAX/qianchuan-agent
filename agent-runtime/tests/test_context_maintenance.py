import importlib.util
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import sqlite3
from contextlib import closing
from datetime import timedelta
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_trading_context import ContextTests, NOW, A, OTHER
from agent import context_sources, analysis_gate


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.helper=ContextTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.root=self.helper.root;self.home=self.helper.home
        self.assertIsNotNone(importlib.util.find_spec('agent.context_diagnostics'),'数据排查和补采功能尚未实现')
        from agent import context_diagnostics
        self.api=context_diagnostics

    def env(self):return {'QC_ADVERTISER_IDS':A,'QC_ACCESS_TOKEN':'dummy-not-a-real-token'}

    def daily(self):
        rows=[{'计划ID':'100','计划名称':'马甲','日期':'2026-10-28','小时区间':'全部',
               '整体成交金额':'320','整体消耗':'50','净成交金额':'200','综合成本':'50'}]
        meta={'advertiser_id':A,'start':'2026-10-28','end':'2026-10-28','days':1,
              'data_topic':analysis_gate.TOPICS['plan'],'monetary_unit':'CNY','fetched_at':NOW.isoformat(),'pages':[]}
        return rows,meta

    def test_pair_gap_is_explained_without_changing_gate(self):
        self.helper.snapshots((90,30,0))
        result=self.api.inspect(self.root,A,now=NOW)
        plan=result['snapshots']['plan']
        self.assertEqual(plan['admission']['reasons'],['gap'])
        self.assertEqual(plan['recent_pairs'][0]['elapsed_minutes'],'60.0')
        self.assertFalse(plan['recent_pairs'][0]['bucket_consecutive'])
        self.assertFalse(plan['recent_pairs'][0]['interval_25_to_40_minutes'])

    def test_late_point_short_interval_is_explained(self):
        series=self.helper.snapshots((60,30,0));at=(NOW-timedelta(minutes=24)).isoformat()
        series[1]['request_end']=at;series[1]['fetched_at']=at
        with closing(sqlite3.connect(self.root/'data/qianchuan/monitor.db')) as db,db:
            db.execute('UPDATE snapshots SET payload=? WHERE bucket=?',(json.dumps(series[1]),series[1]['bucket']))
        p=self.api.inspect(self.root,A,now=NOW)['snapshots']['plan']
        self.assertEqual(p['recent_pairs'][-1]['elapsed_minutes'],'24.0')
        self.assertTrue(p['recent_pairs'][-1]['bucket_consecutive'])
        self.assertFalse(p['recent_pairs'][-1]['interval_25_to_40_minutes'])

    def test_history_exclusion_counts_and_scope(self):
        self.helper.dump('out/model_other.json',self.helper.report(OTHER))
        bad=self.helper.report();bad['source_api']['data_topic']='OTHER'
        self.helper.dump('out/model_bad.json',bad)
        self.helper.dump('out/model_cards_cards.json',[])
        result=self.api.inspect(self.root,A,now=NOW)['history']
        self.assertEqual(result['counts']['other_account'],1)
        self.assertEqual(result['counts']['cards_excluded'],1)
        self.assertEqual(result['excluded'][0]['reason'],'topic_or_unit_mismatch')
        self.assertNotIn(OTHER,json.dumps(result))

    def test_collect_yesterday_populates_context_only_cache(self):
        with patch('agent.qianchuan.fetch_daily',return_value=self.daily()) as fetch:
            result=self.api.collect_yesterday(self.root,A,self.env(),now=NOW)
        fetch.assert_called_once()
        self.assertTrue(result['created'])
        self.assertFalse((self.root/'out').exists())
        context=self.helper.build()
        history=context['entries']['business_facts']['history']
        self.assertEqual(len(history),1)
        self.assertIsNone(history[0]['calculations']['predicted_profit'])
        self.assertEqual(history[0]['calculations']['payment_roi'],'6.4')
        self.assertIn('02_data/history',history[0]['source'])

    def test_existing_yesterday_is_reused_without_api(self):
        self.helper.dump('out/model_ok.json',self.helper.report())
        with patch('agent.qianchuan.fetch_daily',side_effect=AssertionError('不应重复采集')):
            result=self.api.collect_yesterday(self.root,A,self.env(),now=NOW)
        self.assertFalse(result['created'])

    def test_collect_account_mismatch_is_rejected_before_request(self):
        with patch('agent.qianchuan.fetch_daily') as fetch,self.assertRaises(ValueError):
            self.api.collect_yesterday(self.root,A,{'QC_ADVERTISER_IDS':OTHER},now=NOW)
        fetch.assert_not_called()

    def test_collect_wrong_source_topic_is_rejected(self):
        rows,meta=self.daily();meta['data_topic']='OTHER'
        with patch('agent.qianchuan.fetch_daily',return_value=(rows,meta)),self.assertRaises(ValueError):
            self.api.collect_yesterday(self.root,A,self.env(),now=NOW)
        self.assertEqual(list((self.home/'02_data').rglob('model_*.json')),[])

    def test_collect_duplicate_rows_is_rejected(self):
        rows,meta=self.daily();rows*=2
        with patch('agent.qianchuan.fetch_daily',return_value=(rows,meta)),self.assertRaises(ValueError):
            self.api.collect_yesterday(self.root,A,self.env(),now=NOW)

    def test_budget_refresh_uses_valid_current_plan_ids(self):
        self.helper.snapshots()
        value={'advertiser_id':A,'kind':'current_budget','fetched_at':NOW.isoformat(),
               'plans':[{'plan_id':'100','fetched_at':NOW.isoformat(),'status':'ok','amount':'300','mode':'BUDGET_MODE_DAY'}]}
        with patch('agent.budgets.fetch',return_value=value) as fetch:
            result=self.api.refresh_budgets(self.root,A,self.env(),now=NOW)
        self.assertEqual(fetch.call_args.args[2],[{'plan_id':'100'}])
        self.assertEqual(result['successful'],1)
        self.assertEqual(self.helper.build()['entries']['business_facts']['budgets']['plans'][0]['amount'],'300')

    def test_budget_refresh_refuses_stale_snapshot(self):
        self.helper.snapshots((120,90,60))
        with patch('agent.budgets.fetch') as fetch,self.assertRaises(ValueError):
            self.api.refresh_budgets(self.root,A,self.env(),now=NOW)
        fetch.assert_not_called()

    def test_unavailable_budget_not_marked_success(self):
        self.helper.snapshots()
        value={'advertiser_id':A,'kind':'current_budget','fetched_at':NOW.isoformat(),
               'plans':[{'plan_id':'100','fetched_at':NOW.isoformat(),'status':'unavailable','amount':None,'mode':None}]}
        with patch('agent.budgets.fetch',return_value=value):
            result=self.api.refresh_budgets(self.root,A,self.env(),now=NOW)
        self.assertEqual(result['successful'],0)
        self.assertEqual(result['unavailable'],1)

    def test_inspect_does_not_mutate_source_database(self):
        self.helper.snapshots();path=self.root/'data/qianchuan/monitor.db';raw=path.read_bytes()
        self.api.inspect(self.root,A,now=NOW)
        self.assertEqual(path.read_bytes(),raw)

    def test_future_snapshot_not_used_for_budget_selection(self):
        series=self.helper.snapshots();last=series[-1];at=(NOW+timedelta(minutes=3)).isoformat()
        last.update(request_end=at,fetched_at=at)
        with closing(sqlite3.connect(self.root/'data/qianchuan/monitor.db')) as db,db:
            db.execute('UPDATE snapshots SET payload=? WHERE bucket=?',(json.dumps(last),NOW.isoformat()))
        with patch('agent.budgets.fetch') as fetch,self.assertRaises(ValueError):
            self.api.refresh_budgets(self.root,A,self.env(),now=NOW)
        fetch.assert_not_called()

    def test_collect_nondigit_plan_rejected_before_persistence(self):
        rows,meta=self.daily();rows[0]['计划ID']='not-a-plan'
        with patch('agent.qianchuan.fetch_daily',return_value=(rows,meta)),self.assertRaises(ValueError):
            self.api.collect_yesterday(self.root,A,self.env(),now=NOW)
        self.assertEqual(list((self.home/'02_data').rglob('model_*.json')),[])

    def test_budget_unknown_mode_rejected(self):
        self.helper.snapshots()
        value={'advertiser_id':A,'kind':'current_budget','fetched_at':NOW.isoformat(),
               'plans':[{'plan_id':'100','fetched_at':NOW.isoformat(),'status':'ok','amount':'300','mode':'UNKNOWN'}]}
        with patch('agent.budgets.fetch',return_value=value),self.assertRaises(ValueError):
            self.api.refresh_budgets(self.root,A,self.env(),now=NOW)

    def test_cli_all_budget_reads_unavailable_exits_nonzero(self):
        path=Path(__file__).resolve().parents[1]/'tools/maintain_context_data.py'
        spec=importlib.util.spec_from_file_location('maintenance_cli',path)
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        capture=io.StringIO()
        with patch.object(sys,'argv',[str(path),'--project',str(self.root),'--account',A,'--refresh-budgets']), \
             patch('agent.settings.load_env',return_value=self.env()), \
             patch('agent.context_diagnostics.refresh_budgets',return_value={'requested':2,'successful':0,'unavailable':2}), \
             redirect_stdout(capture):
            result=cli.main()
        self.assertEqual(result,1)
        self.assertEqual(json.loads(capture.getvalue())['actions']['budgets']['status'],'failed')

if __name__=='__main__':unittest.main()
