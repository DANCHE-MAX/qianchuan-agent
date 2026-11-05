import copy
from datetime import timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_trading_context as fixtures
from agent import context_finance as finance

A,NOW=fixtures.A,fixtures.NOW


class SeasonalTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.ContextTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.root=self.helper.root;self.home=self.helper.home
        self.assertIsNotNone(importlib.util.find_spec('agent.seasonal_refunds'),'季节整体情景尚未接入')
        from agent import seasonal_refunds
        self.module=seasonal_refunds
        self.helper.dump('config/rules.json',{'model':{'refund_rates':copy.deepcopy(self.module.CONFIRMED_TABLE)},
                                           'preserve':'cloud-custom'})

    def enable(self,season='autumn_winter'):
        return self.module.configure(self.root,A,season,apply=True,now=NOW)

    def rules(self):return json.loads((self.home/'01_rules/rules.json').read_text(encoding='utf-8'))

    def test_preview_does_not_write(self):
        path=self.home/'01_rules/rules.json';before=path.read_bytes()
        result=self.module.configure(self.root,A,'autumn_winter',now=NOW)
        self.assertFalse(result['applied']);self.assertEqual(path.read_bytes(),before)

    def test_apply_preserves_costs_and_existing_configuration(self):
        before=self.rules();source=(self.root/'config/rules.json').read_bytes()
        result=self.enable();after=self.rules()
        for key in before:
            if key!='rule_version':self.assertEqual(after[key],before[key])
        self.assertEqual((self.root/'config/rules.json').read_bytes(),source)
        self.assertTrue((self.root/result['backup']).exists())
        self.assertEqual(after['seasonal_refund_policy']['mode'],'seasonal_overall')

    def test_repeated_apply_is_idempotent(self):
        self.enable();before=(self.home/'01_rules/rules.json').read_bytes()
        result=self.enable()
        self.assertFalse(result['changed']);self.assertEqual((self.home/'01_rules/rules.json').read_bytes(),before)

    def test_reference_breakeven_lines(self):
        self.enable();ref=self.module.references(self.rules())
        self.assertAlmostEqual(float(ref['summer']['breakeven_payment_roi']),3.327078425892655)
        self.assertAlmostEqual(float(ref['autumn_winter']['breakeven_payment_roi']),3.045122627088193)

    def test_seasonal_historical_report_recomputes_without_sku(self):
        self.enable();r=self.helper.report();r['season']='summer'
        self.helper.dump('out/model_summer.json',r)
        p=self.helper.build();plan=p['entries']['business_facts']['history'][0]['plans'][0]
        calculated=plan['calculations']
        self.assertEqual(calculated['refund_rate'],'0.46')
        expected=Decimal('320')*(1-Decimal(calculated['refund_rate']))*Decimal('.5566')-50
        self.assertEqual(Decimal(calculated['predicted_profit']),expected)
        self.assertEqual(calculated['refund_season'],'summer')

    def test_unlabelled_cache_uses_active_season_and_marks_basis(self):
        self.enable();self.helper.dump('out/model_cache.json',self.helper.report())
        p=self.helper.build();plan=p['entries']['business_facts']['history'][0]['plans'][0]
        self.assertEqual(plan['calculations']['refund_rate'],'0.41')
        self.assertEqual(plan['refund_season_basis'],'active_configuration_fallback')
        self.assertNotIn('scoped_refund_assumption_missing',p['data_gaps'])
        self.assertNotIn('historical_plan_refund_assumption_missing_or_ambiguous',p['data_gaps'])

    def test_current_snapshot_still_has_no_predicted_profit(self):
        self.enable();self.helper.snapshots()
        p=self.helper.build()
        self.assertIsNone(p['entries']['business_facts']['current']['calculations']['predicted_profit'])
        self.assertIn('seasonal_reference',p['entries']['business_rules'])

    def test_unknown_report_season_does_not_fallback(self):
        self.enable();r=self.helper.report();r['season']='UNKNOWN'
        self.helper.dump('out/model_unknown.json',r)
        p=self.helper.build();c=p['entries']['business_facts']['history'][0]['calculations']
        self.assertIsNone(c['predicted_profit'])

    def test_policy_does_not_apply_to_other_account_or_live(self):
        self.enable();r=self.rules()
        for patch in ({'advertiser_id':fixtures.OTHER},{'scene':'live'}):
            match={'advertiser_id':A,'scene':'goods','season':'autumn_winter','start':'2026-10-28','end':'2026-10-28',**patch}
            self.assertIsNone(finance.calculate('320','50',r,match)['refund_rate'])

    def test_uniform_policy_does_not_use_legacy_price_band_or_sku_rate(self):
        self.enable();r=self.rules()
        r['refund_assumptions']=[{'advertiser_id':A,'sku':'s','scene':'goods','rate':'.1','status':'CONFIRMED',
            'evidence':'legacy','basis':'full_amount_refund_scenario','valid_from':'2026-10-01','valid_to':'2026-10-31'}]
        match={'advertiser_id':A,'scene':'goods','sku':'s','season':'summer','start':'2026-10-28','end':'2026-10-28'}
        self.assertEqual(finance.calculate('320','50',r,match)['refund_rate'],'0.46')

    def test_old_source_table_mismatch_blocks_apply(self):
        path=self.root/'config/rules.json';cfg=json.loads(path.read_text());cfg['model']['refund_rates']['summer']['overall']=.1
        path.write_text(json.dumps(cfg));before=(self.home/'01_rules/rules.json').read_bytes()
        with self.assertRaises(ValueError):self.enable()
        self.assertEqual((self.home/'01_rules/rules.json').read_bytes(),before)

    def test_invalid_policy_rate_blocks_context(self):
        self.enable();path=self.home/'01_rules/rules.json';r=self.rules()
        r['seasonal_refund_policy']['rates']['summer']='NaN';path.write_text(json.dumps(r))
        with self.assertRaises(ValueError):self.helper.build()

    def test_switch_season_is_explicit(self):
        self.enable();self.module.configure(self.root,A,'summer',apply=True,now=NOW+timedelta(minutes=1))
        self.helper.dump('out/model_cache.json',self.helper.report())
        plan=self.helper.build()['entries']['business_facts']['history'][0]['plans'][0]
        self.assertEqual(plan['calculations']['refund_season'],'summer')

    def test_unknown_season_rejected(self):
        with self.assertRaises(ValueError):self.enable('winter')

    def test_uniform_total_has_explicit_refund_season(self):
        self.enable();self.helper.dump('out/model_cache.json',self.helper.report())
        c=self.helper.build()['entries']['business_facts']['history'][0]['calculations']
        self.assertEqual(c['refund_rate'],'0.41')
        self.assertEqual(c['refund_season'],'autumn_winter')

    def test_preview_does_not_initialize_or_create_lock(self):
        before={p.relative_to(self.root).as_posix() for p in self.root.rglob('*')}
        self.module.configure(self.root,A,'autumn_winter',now=NOW)
        self.module.configure(self.root,fixtures.OTHER,'autumn_winter',now=NOW)
        self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob('*')},before)

if __name__=='__main__':unittest.main()
