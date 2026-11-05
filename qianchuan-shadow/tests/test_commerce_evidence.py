import unittest
from qc_shadow.commerce_evidence import normalize,compare,settlement_view,FIELDS
class CommerceTests(unittest.TestCase):
 def row(self,missing=False):
  m={v:{'Value':'10'} for v in FIELDS.values()}
  if missing:m.pop(FIELDS['net_1h'])
  return {'dimensions':{'ad_id':{'Value':'2'},'stat_time_day':{'ValueStr':'2026-11-03'}},'metrics':m}
 def test_missing_metric_is_unknown(self):
  r=normalize([self.row(True)],'plan','2026-11-03','2026-11-03');self.assertIsNone(r[0]['net_1h'])
 def test_duplicate_rejected(self):
  with self.assertRaises(ValueError):normalize([self.row(),self.row()],'plan','2026-11-03','2026-11-03')
 def test_wrong_date_rejected(self):
  with self.assertRaises(ValueError):normalize([self.row()],'plan','2026-11-02','2026-11-02')
 def test_reconciliation_does_not_accept_unknown(self):
  r=normalize([self.row(True)],'plan','2026-11-03','2026-11-03');t=[dict(r[0],plan_id=None)];self.assertFalse(compare(r,t)['2026-11-03'])
 def test_yesterday_30day_value_is_provisional(self):
  r=normalize([self.row()],'plan','2026-11-03','2026-11-03')[0];v=settlement_view(r,'2026-11-04');self.assertFalse(v['settlement_windows']['30']['elapsed']);self.assertFalse(v['actual_profit_verified'])
 def test_30day_age_uses_closed_day_end(self):
  r=normalize([self.row()],'plan','2026-11-03','2026-11-03')[0];self.assertFalse(settlement_view(r,'2026-12-03')['settlement_windows']['30']['elapsed']);self.assertTrue(settlement_view(r,'2026-12-04')['settlement_windows']['30']['elapsed'])
 def test_supplement_never_fills_missing_base_or_changes_it(self):
  from qc_shadow.commerce_evidence import attach
  p={'plan_id':'2','daily':[]};r=normalize([self.row()],'plan','2026-11-03','2026-11-03')
  v=attach(p,{'status':'ok','rows':r,'reconciliation':{'2026-11-03':True}},'2026-11-04')
  self.assertEqual(p['daily'],[]);self.assertEqual(len(v['days']),1)
 def test_core_revision_is_flagged(self):
  from qc_shadow.commerce_evidence import attach
  p={'plan_id':'2','daily':[{'day':'2026-11-03','spend':'11','gmv':'10','orders':10}]};r=normalize([self.row()],'plan','2026-11-03','2026-11-03')
  v=attach(p,{'status':'ok','rows':r,'reconciliation':{'2026-11-03':True}},'2026-11-04');self.assertEqual(v['status'],'revision_conflict')
 def test_transient_failure_bounded_retry_with_hour_backoff(self):
  from qc_shadow.commerce_evidence import ensure
  from qc_shadow.read_transport import ReadFailure
  from unittest.mock import patch,MagicMock
  from tempfile import TemporaryDirectory
  from datetime import datetime
  client=MagicMock();client.account='1';client.get.side_effect=ReadFailure(code='transport_or_json')
  with TemporaryDirectory() as t,patch('qc_shadow.commerce_evidence.ReadClient.from_runtime',return_value=client) as factory,patch('qc_shadow.daily_refresh.time.sleep'):
   # Paced's default sleep function is bound at definition, replace the wrapper instead.
   with patch('qc_shadow.commerce_evidence.Paced',side_effect=lambda c:type('Fast',(),{'client':c,'receipts':[],'get':lambda self,*a:c.get(*a)})()):
    for tm in ('10:00','10:30','11:01','12:02','13:03'):ensure(t,'unused','1',datetime.fromisoformat('2026-11-04T'+tm+':00+08:00'))
   self.assertEqual(factory.call_count,3)
