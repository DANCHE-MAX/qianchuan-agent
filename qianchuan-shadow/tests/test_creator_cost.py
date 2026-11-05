import unittest
from qc_shadow.creator_cost import annotate,AUTHORITY,budget_cost_ready
class CreatorCostTests(unittest.TestCase):
 def report(self):return {'windows':{'1':{'estimated_profit':'100'},'7':{'estimated_profit':None}},'intraday':{'estimated_profit':'2'},'gaps':['creator_attribution_and_refund_reversals_missing','daily_revision_review_pending'],'cost_context_verified':False}
 def test_user_self_operated_confirmation_is_zero_fee_basis(self):
  r=annotate(self.report())
  self.assertEqual(r['windows']['1']['estimated_creator_commission'],'0')
  self.assertEqual(r['windows']['1']['estimated_profit_after_creator_commission'],'100')
  self.assertIsNone(r['windows']['7']['estimated_profit_after_creator_commission'])
  self.assertNotIn('creator_attribution_and_refund_reversals_missing',r['gaps'])
  self.assertIn('daily_revision_review_pending',r['gaps'])
  self.assertEqual(r['creator_commission']['status'],'user_confirmed_no_creator_commission')
  self.assertIsNone(AUTHORITY['creator_rate'])
 def test_only_commission_gate_is_satisfied(self):
  self.assertTrue(budget_cost_ready('9000000000000099'))
 def test_star_remains_excluded(self):
  self.assertEqual(annotate(self.report())['star_cost']['amount'],'0')
 def test_other_cost_evidence_gaps_remain(self):
  r=self.report();r['gaps']+=['product_binding_changed','roi_cost_flags_conflict'];annotate(r)
  self.assertIn('product_binding_changed',r['gaps']);self.assertFalse(r['cost_context_verified'])
