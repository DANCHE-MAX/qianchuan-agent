import unittest
from qc_shadow.history import first_enabled,parse_daily,reconcile

def logbundle(lines=None):
 return {'account_id':'9','plan_id':'1','create_time':'2026-10-01 12:00:01','query_start':'2026-10-01 00:00:00','query_end':'2026-11-02T12:00:00+08:00','pages_complete':True,'pages':[{'code':0,'data':{'page_info':{'page':1,'total_page':1,'total_number':1},'logs':[{'object_id':1,'log_id':'event','create_time':'2026-10-01 12:00:00','content_title':'新建','content_log':lines or ['是否开启: 开启']} ]}}]}
def dailybundle(rows=None,granularity='plan'):
 row={'dimensions':{'ad_id':{'Value':'1'},'stat_time_day':{'ValueStr':'2026-11-01'}},'metrics':{'stat_cost_for_roi2':{'Value':'100000'},'total_pay_order_gmv_include_coupon_for_roi2':{'Value':'200000'},'total_pay_order_count_for_roi2':{'Value':0}}}
 rows=[row] if rows is None else rows
 return {'account_id':'9','requested_start':'2026-11-01','requested_end':'2026-11-01','fetched_at':'2026-11-02T10:00:00+08:00','params':{'data_topic':'OVERALL_ROI_PRODUCT_PROJECT'},'granularity':granularity,'pages_complete':True,'pages':[{'code':0,'data':{'page_info':{'page':1,'total_page':1,'total_number':len(rows)},'rows':rows}}]}
class HistoryTests(unittest.TestCase):
 def test_explicit_enable_not_create_time(self):
  r=first_enabled(logbundle());self.assertEqual(r['first_enabled_at'],'2026-10-01T12:00:00+08:00');self.assertTrue(r['verified'])
 def test_creative_enable_not_plan_enable(self):self.assertFalse(first_enabled(logbundle(['AIGC动态创意: 开启']))['verified'])
 def test_no_complete_logs_no_first(self):
  b=logbundle();b['pages_complete']=False;self.assertFalse(first_enabled(b)['verified'])
 def test_empty_or_create_only_not_first(self):self.assertFalse(first_enabled(logbundle(['预算: 日预算300元']))['verified'])
 def test_wrong_object_rejected(self):
  b=logbundle();b['pages'][0]['data']['logs'][0]['object_id']=2;self.assertFalse(first_enabled(b)['verified'])
 def test_reenable_does_not_reset(self):
  b=logbundle();p=b['pages'][0]['data'];p['logs'].append(dict(p['logs'][0],log_id='again',create_time='2026-10-20 12:00:00',content_title='修改',content_log=['修改 乘方状态: 系统暂停 -> 开启']));p['page_info']['total_number']=2
  self.assertEqual(first_enabled(b)['first_enabled_at'][:10],'2026-10-01')
 def test_daily_preserves_yuan_and_zero_order(self):
  r=parse_daily(dailybundle());self.assertEqual(r[0]['spend'],'100000');self.assertEqual(r[0]['orders'],0)
 def test_missing_is_not_zero(self):self.assertEqual(parse_daily(dailybundle([])),[])
 def test_missing_metric_rejects(self):
  b=dailybundle();del b['pages'][0]['data']['rows'][0]['metrics']['total_pay_order_count_for_roi2']
  with self.assertRaises(ValueError):parse_daily(b)
 def test_current_day_rejected(self):
  b=dailybundle();b['fetched_at']='2026-11-01T23:59:59+08:00'
  with self.assertRaises(ValueError):parse_daily(b)
 def test_duplicate_row_rejected(self):
  b=dailybundle();b['pages'][0]['data']['rows']*=2;b['pages'][0]['data']['page_info']['total_number']=2
  with self.assertRaises(ValueError):parse_daily(b)
 def test_reconcile_exact(self):
  plan=parse_daily(dailybundle());day=parse_daily(dailybundle(granularity='day'))
  self.assertEqual(reconcile(plan,day),{'2026-11-01':True});day[0]['gmv']='199999';self.assertEqual(reconcile(plan,day),{'2026-11-01':False})
