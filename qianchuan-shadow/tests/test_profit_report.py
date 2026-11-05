import unittest
from qc_shadow.profit_report import comparison_report, reminder_draft

class ReportTests(unittest.TestCase):
    def result(self):
        return {'profile':{'start':'2026-10-21','end':'2026-11-03','account_id':'acct','version':'v3'},
            'model_version':'v3','products':[{'style':'S','gross_income':'100.00','pre_ad_profit':'30.00',
            'actual_ad_spend':'20.00','estimated_ad_spend':'15.00','post_ad_profit_actual':'10.00',
            'post_ad_profit_estimated':'15.00','break_even_additional_spend':'10.00',
            'recommended_additional_spend':'0.00','actual_over_estimated_ad':'5.00','alert_required':True}]}
    def test_draft_shows_actual_and_estimated_without_sending(self):
        s=reminder_draft(self.result())
        for text in ('已花20.00元','剩10.00元','15%','15.00元','建议还能投0.00元','未发送','2026-10-21','不是今天'):
            self.assertIn(text,s)
    def test_unknown_does_not_become_zero(self):
        r=self.result()
        for k in ('actual_ad_spend','post_ad_profit_actual','break_even_additional_spend','recommended_additional_spend','actual_over_estimated_ad'):r['products'][0][k]=None
        self.assertIn('待核验',reminder_draft(r));self.assertIn('待核验',comparison_report(r))
        self.assertNotIn('已花0.00元',reminder_draft(r))
