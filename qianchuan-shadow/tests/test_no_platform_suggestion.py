import unittest
from types import SimpleNamespace
from qc_shadow.roi_source import capture,RoiReadClient
from qc_shadow.roi_shadow import analyze_plan
import test_roi_shadow
class NoSuggestionTests(unittest.TestCase):
 def test_only_detail_is_allowed(self):self.assertEqual(RoiReadClient.ALLOWED,{RoiReadClient.DETAIL})
 def test_capture_never_requests_suggestion(self):
  calls=[]
  def get(path,params):
   calls.append(path)
   if path!=RoiReadClient.DETAIL:raise AssertionError('suggestion_requested')
   return {'data':{'ad_id':2,'marketing_goal':'VIDEO_PROM_GOODS','delivery_setting':{'roi2_goal':6},'product_infos':[]}}
  r=capture(SimpleNamespace(client=SimpleNamespace(account='1'),get=get),'2');self.assertEqual(r['status'],'ok');self.assertEqual(calls,[RoiReadClient.DETAIL]);self.assertFalse(any('recommend' in k for k in r))
 def test_legacy_recommendation_inputs_cannot_affect_analysis(self):
  p,s=test_roi_shadow.RoiTests().fixture();a=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');s.update(recommended_roi='1000',recommendation_status='failed',recommendation_basis_verified=False);b=analyze_plan(p,s,'2026-11-04T10:36:00+08:00');self.assertEqual(a,b);self.assertIsNone(a['candidate_roi']);self.assertFalse(any('recommendation' in g for g in a['gaps']))
