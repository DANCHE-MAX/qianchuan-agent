import copy, tempfile, unittest
from decimal import Decimal as D
from pathlib import Path
from qc_shadow.profit import ProfitStore, calculate, validate_profile, recommend_spend

def profile():
    return {'version':'test-v1','account_id':'a','start':'2026-10-21','end':'2026-10-21',
            'fees':{'service':'0.131'},'shipping':'2.2','minimum_profit':None,
            'products':{'p':{'style':'S','cost':'10'}}}

def rows():
    return [('orders','o',{'id':'o','parent':'m','product':'p','paid':'2026-10-21','shipped':True,'quantity':2,'income':'100','source':'test:2'}),
            ('refunds','o',{'order':'o','success':False,'status':'-','source':'test:2','basis':'order_status_proxy'}),
            ('ads','p/plan/2026-10-21',{'product':'p','day':'2026-10-21','spend':'5','source':'test:3'})]

class ProfitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'profit.db';self.s=ProfitStore(self.path)
        self.s.ingest(rows(),'hash1','2026-11-04T16:27:00+08:00')
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def result(self):return calculate(self.s,profile())['products'][0]
    def test_calculation_and_no_execution(self):
        r=calculate(self.s,profile());self.assertEqual(r['products'][0]['post_ad_profit'],'59.70')
        self.assertFalse(r['execution_allowed']);self.assertIsNone(r['products'][0]['allowed_ad_spend'])
    def test_repeat_and_reopen(self):
        n=self.s.change_count();self.s.ingest(rows(),'hash1','2026-11-04T16:27:00+08:00');self.assertEqual(n,self.s.change_count())
        self.s.close();self.s=ProfitStore(self.path);self.assertEqual(self.result()['post_ad_profit'],'59.70')
    def test_refund_then_return(self):
        r=rows()[1];r[2]['success']=True;r[2]['status']='退款成功'
        self.s.ingest([r],'hash2','2026-11-05T10:00:00+08:00')
        self.assertEqual(self.result()['post_ad_profit'],'-27.20')
        self.s.ingest([('returns','o',{'order':'o','quantity':2,'evidence':'warehouse:1'})],'hash3','2026-11-05T11:00:00+08:00')
        self.assertEqual(self.result()['post_ad_profit'],'-7.20')
    def test_old_revision_rejected(self):
        r=rows()[0];r[2]['income']='200'
        with self.assertRaises(ValueError):self.s.ingest([r],'older','2026-11-01T00:00:00+08:00')
        self.assertEqual(self.result()['net_income'],'100.00')
    def test_conflicting_duplicate_rolls_back(self):
        r=rows()[0];other=copy.deepcopy(r);other[2]['income']='200'
        with self.assertRaises(ValueError):self.s.ingest([r,other],'conflict','2026-11-05T00:00:00+08:00')
        self.assertEqual(self.result()['net_income'],'100.00')
    def test_shipping_deduplicates_parent(self):
        r=copy.deepcopy(rows()[0]);r=('orders','o2',dict(r[2],id='o2'))
        self.s.ingest([r,('refunds','o2',{'order':'o2','success':False,'status':'-'})],'extra','2026-11-05T00:00:00+08:00')
        self.assertEqual(self.result()['logistics'],'2.20')
    def test_unknown_ad_is_not_zero(self):
        p=profile();p['products']['p2']={'style':'S2','cost':'10'}
        r=calculate(self.s,p)['products'][1];self.assertIsNone(r['post_ad_profit']);self.assertIsNone(r['ad_spend'])
    def test_required_amount_missing_rejected(self):
        r=rows()[0];r[2]['income']=None
        with self.assertRaises(ValueError):self.s.ingest([r],'bad','2026-11-05T00:00:00+08:00')
    def test_return_above_quantity_blocked(self):
        self.s.ingest([('returns','o',{'order':'o','quantity':3,'evidence':'x'})],'excess','2026-11-05T00:00:00+08:00')
        with self.assertRaises(ValueError):calculate(self.s,profile())
    def test_minimum_profit_and_negative_allowance(self):
        p=profile();p['minimum_profit']='100';r=calculate(self.s,p)['products'][0]
        self.assertEqual(r['allowed_ad_spend'],'-35.30')
    def test_invalid_profile(self):
        p=profile();p['fees']['service']='NaN'
        with self.assertRaises(ValueError):validate_profile(p)
    def test_missing_ad_day_cannot_pass_as_zero(self):
        p=profile();p['end']='2026-10-22';r=calculate(self.s,p)['products'][0]
        self.assertFalse(r['ad_days_complete']);self.assertIsNone(r['post_ad_profit'])
    def test_missing_refund_state_blocks_profit(self):
        r=copy.deepcopy(rows()[0]);r=('orders','o2',dict(r[2],id='o2'))
        self.s.ingest([r],'no-refund','2026-11-05T00:00:00+08:00')
        self.assertIsNone(self.result()['net_income']);self.assertIsNone(self.result()['post_ad_profit'])
    def test_audit_preserves_before_after(self):
        r=rows()[1];r[2]['success']=True
        self.s.ingest([r],'revised','2026-11-05T00:00:00+08:00')
        old,new=self.s.db.execute("SELECT before,after FROM changes WHERE source_hash='revised'").fetchone()
        self.assertIn('"success":false',old);self.assertIn('"success":true',new)
    def test_real_zero_spend_is_valid(self):
        r=rows()[2];r[2]['spend']='0'
        self.s.ingest([r],'zero','2026-11-05T00:00:00+08:00')
        self.assertEqual(self.result()['post_ad_profit'],'64.70')

    def test_gmv_ad_and_assumed_return_keep_shipping(self):
        p=profile();p.update(ad_basis='gross_gmv_rate',ad_rate='0.15',profit_target_rate='0.15',return_policy='refund_success_assumed_returned',minimum_profit='0')
        r=rows()[1];r[2].update(success=True,status='退款成功')
        self.s.ingest([r],'refund-v2','2026-11-05T00:00:00+08:00')
        result=calculate(self.s,p);x=result['products'][0]
        self.assertEqual(x['ad_spend'],'15.00');self.assertEqual(x['product_cost'],'0.00')
        self.assertEqual(x['logistics'],'2.20');self.assertEqual(x['post_ad_profit'],'-17.20')
        self.assertEqual(x['recommended_ad_total'],'0.00');self.assertTrue(x['alert_required'])
        self.assertIsNone(result['order_details'][0]['returned_quantity'])
        self.assertEqual(result['order_details'][0]['return_basis'],'business_assumption')

    def test_pending_return_does_not_reverse_cost(self):
        p=profile();p.update(ad_basis='gross_gmv_rate',ad_rate='0.15',profit_target_rate='0.15',return_policy='refund_success_assumed_returned')
        r=rows()[1];r[2]['status']='待收退货';self.s.ingest([r],'pending','2026-11-05T00:00:00+08:00')
        self.assertEqual(calculate(self.s,p)['products'][0]['product_cost'],'20.00')

    def test_threshold_strict_and_break_even(self):
        self.assertTrue(recommend_spend(D('100'),D('30'),D('.15'),D('.15'))['alert_required'])
        self.assertFalse(recommend_spend(D('100'),D('30.01'),D('.15'),D('.15'))['alert_required'])
        r=recommend_spend(D('100'),D('10'),D('.15'),D('.15'))
        self.assertEqual(r['recommended_ad_total'],'10.00')
        self.assertEqual(r['profit_at_recommendation'],'0.00')
        r=recommend_spend(D('100'),D('-1'),D('.15'),D('.15'))
        self.assertEqual(r['recommended_ad_total'],'0.00');self.assertTrue(r['loss_before_ads'])

    def test_estimated_ads_do_not_require_actual_ad_days(self):
        p=profile();p.update(end='2026-10-22',ad_basis='gross_gmv_rate',ad_rate='0.15',profit_target_rate='0.15')
        x=calculate(self.s,p)['products'][0]
        self.assertFalse(x['ad_days_complete']);self.assertEqual(x['ad_spend'],'15.00')
        self.assertEqual(x['reference_plan_spend'],'5.00');self.assertEqual(x['post_ad_profit'],'49.70')

    def test_return_evidence_overrides_assumption(self):
        p=profile();p.update(return_policy='refund_success_assumed_returned')
        r=rows()[1];r[2]['success']=True
        self.s.ingest([r,('returns','o',{'order':'o','quantity':1,'evidence':'warehouse'})],'partial-return','2026-11-05T00:00:00+08:00')
        self.assertEqual(calculate(self.s,p)['products'][0]['product_cost'],'10.00')

    def test_actual_and_estimated_profit_and_additional_are_separate(self):
        p=profile();p.update(ad_basis='gross_gmv_rate',ad_rate='0.15',profit_target_rate='0.15')
        x=calculate(self.s,p)['products'][0]
        self.assertEqual(x['actual_ad_spend'],'5.00')
        self.assertEqual(x['estimated_ad_spend'],'15.00')
        self.assertEqual(x['post_ad_profit_actual'],'59.70')
        self.assertEqual(x['post_ad_profit_estimated'],'49.70')
        self.assertEqual(x['break_even_additional_spend'],'59.70')
        self.assertEqual(x['recommended_additional_spend'],'10.00')

    def test_actual_over_fifteen_percent_can_still_be_profitable(self):
        p=profile();p.update(ad_basis='gross_gmv_rate',ad_rate='0.15',profit_target_rate='0.15')
        r=rows()[2];r[2]['spend']='20';self.s.ingest([r],'over','2026-11-05T00:00:00+08:00')
        x=calculate(self.s,p)['products'][0]
        self.assertEqual(x['post_ad_profit_actual'],'44.70')
        self.assertEqual(x['break_even_additional_spend'],'44.70')
        self.assertEqual(x['recommended_additional_spend'],'0.00')
        self.assertEqual(x['actual_over_estimated_ad'],'5.00')

    def test_actual_loss_does_not_recommend_more(self):
        p=profile();p.update(ad_basis='gross_gmv_rate',ad_rate='0.15',profit_target_rate='0.15')
        r=rows()[2];r[2]['spend']='70';self.s.ingest([r],'loss','2026-11-05T00:00:00+08:00')
        x=calculate(self.s,p)['products'][0]
        self.assertEqual(x['post_ad_profit_actual'],'-5.30')
        self.assertEqual(x['break_even_additional_spend'],'0.00')
        self.assertEqual(x['recommended_additional_spend'],'0.00')

    def test_incomplete_actual_days_leave_actual_profit_unknown(self):
        p=profile();p.update(end='2026-10-22',ad_basis='gross_gmv_rate',ad_rate='0.15',profit_target_rate='0.15')
        x=calculate(self.s,p)['products'][0]
        for k in ('actual_ad_spend','post_ad_profit_actual','break_even_additional_spend','recommended_additional_spend'):
            self.assertIsNone(x[k])
        self.assertEqual(x['post_ad_profit_estimated'],'49.70')

if __name__=='__main__':unittest.main()
