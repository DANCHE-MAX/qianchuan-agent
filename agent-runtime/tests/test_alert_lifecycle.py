import unittest,sqlite3
from agent import alert_lifecycle as life
class LifecycleTests(unittest.TestCase):
 def setUp(self):self.db=sqlite3.connect(':memory:')
 def observe(self,stamp,status,kind='collection',subject='demo'):
  return life.observe(self.db,dict(account='test',kind=kind,subject=subject,stamp=stamp,status=status,name='测试对象'))
 def test_retry_is_idempotent_and_missing_is_not_recovery(self):
  a=self.observe('2026-01-01T10:00:00+08:00','active');self.assertEqual(a['severity'],'attention')
  self.observe('2026-01-01T10:00:00+08:00','active')
  b=self.observe('2026-01-01T10:30:00+08:00','active');self.assertEqual(b['failures'],2);self.assertEqual(b['severity'],'urgent')
  self.assertEqual(self.observe('2026-01-01T11:00:00+08:00','unknown')['status'],'unknown')
  self.assertEqual(life.pending_recoveries(self.db,'test'),[])
 def test_recovery_ack_only_after_success_and_old_samples_ignored(self):
  self.observe('2026-01-01T10:00:00+08:00','active','funds')
  self.observe('2026-01-01T10:30:00+08:00','healthy','funds')
  pending=life.pending_recoveries(self.db,'test');self.assertEqual(len(pending),1)
  self.observe('2026-01-01T09:00:00+08:00','active','funds')
  self.assertEqual(len(life.pending_recoveries(self.db,'test')),1)
  life.ack(self.db,pending);self.assertEqual(life.pending_recoveries(self.db,'test'),[])
 def test_queued_recovery_not_added_to_second_card(self):
  import tempfile,json
  from pathlib import Path
  from agent.monitor import Store
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);store=Store(root)
   life.record(root,'demo','funds','balance','2026-01-01T10:00:00+08:00','active','余额')
   life.record(root,'demo','funds','balance','2026-01-01T10:30:00+08:00','healthy','余额')
   first=life.decorate(root,'demo',{'elements':[]});self.assertTrue(first.get('_incident_recoveries'))
   from datetime import datetime
   store.enqueue('first','demo',datetime.fromisoformat('2026-01-01T10:30:00+08:00'),first)
   self.assertNotIn('_incident_recoveries',life.decorate(root,'demo',{'elements':[]}))

 def test_tracking_failure_does_not_break_monitoring(self):
  from unittest.mock import patch
  with patch.object(life,'_rule_observations',side_effect=sqlite3.OperationalError('locked')):
   self.assertIsNone(life.rule_observations(None,'demo','goods','stamp',[],{}))
