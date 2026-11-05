import tempfile, unittest, sqlite3
from pathlib import Path
from qc_shadow.execution_store import ExecutionStore

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'actions.db';self.s=ExecutionStore(self.path)
 def tearDown(self):self.temp.cleanup()
 def test_baseline_immutable_and_restart(self):
  self.s.freeze('a','p','5000','2026-11-04','proof')
  self.assertEqual(ExecutionStore(self.path).baseline('a','p')['amount'],'5000')
  with self.assertRaises(ValueError):self.s.freeze('a','p','6000','2026-11-05','new')
 def test_unknown_lifecycle_never_defaults_passed(self):
  self.s.observe('a','p',{'first_enabled_on':None,'refund':{'verified':False}})
  row=self.s.latest('a','p');self.assertIsNone(row['first_enabled_on']);self.assertNotIn('stage',row)
 def test_same_intent_never_replayed_even_restart(self):
  self.assertTrue(self.s.reserve('key','a','p',{'after':'6000'}))
  self.assertFalse(ExecutionStore(self.path).reserve('key','a','p',{'after':'6000'}))
  self.assertFalse(self.s.reserve('different','a','p',{'after':'6000'}))
 def test_only_confirmed_success_counts(self):
  self.s.reserve('k','a','p',{});self.s.finish('k','uncertain',{})
  self.assertEqual(self.s.success_count('a','p'),0)
  self.assertFalse(self.s.reserve('next','a','p',{}))
 def test_success_and_immutable_receipts(self):
  self.s.reserve('k','a','p',{});self.s.finish('k','success',{'readback':'6000'})
  self.assertEqual(self.s.success_count('a','p'),1)
  with self.assertRaises(ValueError):self.s.finish('k','failed',{})
 def test_baseline_db_update_prohibited(self):
  self.s.freeze('a','p','5000','2026-11-04','proof')
  with sqlite3.connect(self.path) as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE baselines SET amount='1'")

 def test_replace_cannot_change_baseline_or_receipt(self):
  self.s.freeze('a','p','5000','2026-11-04','proof')
  self.s.reserve('k','a','p',{});self.s.finish('k','uncertain',{})
  with sqlite3.connect(self.path) as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute("INSERT OR REPLACE INTO baselines VALUES('a','p','1','2026-11-04','fake')")
   with self.assertRaises(sqlite3.IntegrityError):db.execute("INSERT OR REPLACE INTO outcomes VALUES('k','now','success','{}')")
 def test_observe_return_to_prior_value_is_latest(self):
  self.s.observe('a','p',{'amount':1});self.s.observe('a','p',{'amount':2});self.s.observe('a','p',{'amount':1})
  self.assertEqual(self.s.latest('a','p'),{'amount':1})
