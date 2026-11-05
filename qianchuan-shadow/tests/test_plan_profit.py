import unittest
from qc_shadow.plan_profit import calculate, report, reconcile, html_report

POLICY = dict(refund_rate='.6', purchase_multiplier='3.2', fee_rate='.131',
              ad_rate='.15', shipping_mode='excluded')  # synthetic refund rate

class PlanProfitTests(unittest.TestCase):
    def test_scenario_and_rounding(self):
        r=calculate('1000','100',POLICY)
        self.assertEqual((r['net'],r['cost'],r['fee'],r['profit'],r['candidate']),
                         ('400.00','125.00','52.40','122.60','50.00'))
        self.assertEqual(calculate('784.90','0',POLICY)['ad15'],'117.74')

    def test_zero_evidence_does_not_create_gmv_or_profit(self):
        r=report([dict(plan_id='demo',name='商品示例')],[],
                 [dict(plan_id='demo',date='2026-01-01',goal='VIDEO_PROM_GOODS',request_id='synthetic')],
                 POLICY,date='2026-01-01')
        self.assertEqual(r['rows'][0]['spend'],'0.00')
        self.assertIsNone(r['rows'][0]['profit'])
        self.assertIsNone(r['rows'][0]['candidate'])
        self.assertFalse(r['execution_enabled'])
        self.assertFalse(r['feedback_required'])

    def test_conflicting_zero_and_duplicate_reports_rejected(self):
        day=dict(plan_id='demo',name='test',date='2026-01-01',gmv='100',spend='1')
        zero=dict(plan_id='demo',date=day['date'],goal='VIDEO_PROM_GOODS',request_id='synthetic')
        for rows, evidence in [([day,day],[]),([day],[zero])]:
            with self.assertRaises(ValueError):report([],rows,evidence,POLICY,date=day['date'])

    def test_live_zero_not_allowed(self):
        with self.assertRaises(ValueError):
            report([],[],[dict(plan_id='demo',date='2026-01-01',goal='LIVE_PROM_GOODS',request_id='synthetic')],POLICY,date='2026-01-01')

    def test_missing_control_is_unknown(self):
        self.assertIsNone(reconcile([],None))
        self.assertEqual(reconcile(['1.10','2.20'],'3.30'),'0.00')

    def test_loss_missing_and_html_escaping(self):
        r=report([dict(plan_id='missing',name='缺失商品')],
                 [dict(plan_id='demo',name='<商品>',date='2026-01-01',gmv='0',spend='10')],[],POLICY,date='2026-01-01')
        self.assertEqual(r['rows'][0]['candidate'],'0.00')
        self.assertEqual(r['rows'][0]['profit'],'-10.00')
        self.assertIn('&lt;商品&gt;',html_report(r))
        self.assertIn('class="loss"',html_report(r))
        self.assertEqual(r['missing_profit'],1)
