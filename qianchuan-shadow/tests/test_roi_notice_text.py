import unittest,tempfile,json
from pathlib import Path
from qc_shadow.roi_notice_text import lines,previous_names
class NoticeText(unittest.TestCase):
 def test_named_rate_failure(self):
  r={'plans':[{'plan_id':'p','name':'虚构外套'}],'errors':[{'plan_id':'p','code':40100}]}
  t=''.join(lines(r));self.assertIn('虚构外套：接口限流',t);self.assertIn('当前目标ROI',t);self.assertIn('暂不提供',t);self.assertNotIn('latest.json',t)
 def test_unknown_and_component_failure(self):
  self.assertIn('计划p',lines({'errors':[{'plan_id':'p'}]})[0])
  self.assertIn('净成交与日报数据不一致',lines({'errors':[{'component':'commerce_revision','plan_id':'p'}]})[0])
  self.assertIn('历史日报补采失败',lines({'errors':[{'component':'daily_recovery'}]})[0])
 def test_historical_name_never_reuses_target(self):
  with tempfile.TemporaryDirectory() as tmp:
   d=Path(tmp)/'settings';d.mkdir()
   (d/'2.json').write_text(json.dumps({'account_id':'other','settings':[{'plan_id':'p','status':'ok','name':'错账号'}]}))
   (d/'1.json').write_text(json.dumps({'account_id':'a','settings':[{'plan_id':'p','status':'ok','name':'虚构外套','target_roi':999}]}))
   p=[{'plan_id':'p','account_id':'a','name':None}];names=previous_names(tmp,p)
   t=''.join(lines({'plans':p,'errors':[{'plan_id':'p','code':40100}]},names));self.assertIn('上次采集名称',t);self.assertNotIn('999',t);self.assertNotIn('错账号',t)
 def test_bounded_and_generic(self):
  self.assertIn('另有2项',lines({'errors':[{}]*10})[-2])
