import unittest,tempfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock
from qc_shadow.daily_refresh import changes,Paced,ensure,acquire,write
from qc_shadow.read_transport import ReadFailure
class DailyRefreshTests(unittest.TestCase):
 def row(self,day='2026-11-02',value='1'):return dict(plan_id='1',day=day,spend=value,gmv='2',orders=1)
 def test_new_day_not_revision(self):self.assertEqual(changes([self.row()],[self.row(),self.row('2026-11-03')]),[])
 def test_revision_detected(self):self.assertEqual(len(changes([self.row()],[self.row(value='3')])),1)
 def test_disappearing_row_detected(self):self.assertEqual(len(changes([self.row()],[])),1)
 def test_appearing_old_day_detected(self):self.assertEqual(len(changes([self.row()],[self.row(),self.row('2026-11-01')])),1)
 def test_rate_limit_bounded(self):
  c=Mock();c.get.side_effect=ReadFailure(code=40100,category='rate_limited');s=Mock();p=Paced(c,s)
  with self.assertRaises(ReadFailure):p.get('x',{})
  self.assertEqual(c.get.call_count,3);self.assertEqual(len(p.receipts),3)
 def test_unknown_40100_not_retried(self):
  c=Mock();c.get.side_effect=ReadFailure(code=40100);p=Paced(c,Mock())
  with self.assertRaises(ReadFailure):p.get('x',{})
  self.assertEqual(c.get.call_count,1)
 def test_invalid_pagination_stops(self):
  c=Mock();c.client.account='1';c.get.return_value={'data':{'page_info':{'page':2,'total_number':0,'total_page':1},'rows':[]}}
  with self.assertRaises(ValueError):acquire(c,'2026-11-01','2026-11-02','plan',None)
 def test_before_due_blocks_old_history(self):
  with tempfile.TemporaryDirectory() as d:
   r=ensure(Path(d)/'daily',Path(d)/'production',Path(d)/'old',datetime(2026,11,4,8,tzinfo=ZoneInfo('Asia/Shanghai')))
   self.assertEqual(r['status'],'waiting_daily_refresh');self.assertIsNone(r['history'])
 def test_revision_block_does_not_retry(self):
  with tempfile.TemporaryDirectory() as d:
   h=Path(d)/'daily';h.mkdir();write(h/'daily_status.json',{'collection_scope':'post-20261021-v1','status':'revision_review_required','target_day':'2026-11-03','attempts':1})
   r=ensure(h,Path(d)/'production',Path(d)/'old',datetime(2026,11,4,12,tzinfo=ZoneInfo('Asia/Shanghai')))
   self.assertEqual(r['status'],'revision_review_required');self.assertIsNone(r['history'])
 def test_production_destination_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):ensure(d,d,'unused')

 def test_revision_retained_when_window_moves_and_no_new_changes(self):
  from qc_shadow.daily_refresh import retain_revisions
  pending=changes([self.row('2026-10-28')],[self.row('2026-10-28','3')])
  self.assertEqual(retain_revisions(pending,[]),pending)
  self.assertEqual(retain_revisions(pending,pending),pending)
