import unittest,tempfile,importlib.util
from pathlib import Path
from unittest.mock import patch,Mock
spec=importlib.util.spec_from_file_location('worker',Path(__file__).resolve().parents[1]/'tools/continuous_shadow.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
class WorkerTests(unittest.TestCase):
 def test_rejects_production_destination(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):worker.run(d,d,'unused')
 def test_refuses_execution_result(self):
  with tempfile.TemporaryDirectory() as d:
   source=Mock();source.accounts.return_value=['1'];source.snapshot.return_value={'plans':[],'account_id':'1','as_of':'2026-11-04T10:00:00+08:00'}
   with patch.object(worker,'Source',return_value=source),patch.object(worker,'shadow',return_value={'execution_allowed':True,'rule_live_on':None}):
    with self.assertRaises(ValueError):worker.run(Path(d)/'shadow',Path(d)/'production','unused')
   self.assertFalse((Path(d)/'shadow/shadow.db').exists())
 def test_saves_local_shadow(self):
  with tempfile.TemporaryDirectory() as d:
   source=Mock();source.accounts.return_value=['1'];source.snapshot.return_value={'plans':[],'account_id':'1','as_of':'2026-11-04T10:00:00+08:00'}
   with patch.object(worker,'Source',return_value=source),patch.object(worker,'shadow',return_value={'execution_allowed':False,'rule_live_on':None}):
    r=worker.run(Path(d)/'shadow',Path(d)/'production','unused')
   self.assertEqual(r['status'],'ok');self.assertFalse(r['execution_allowed']);self.assertTrue((Path(d)/'shadow/status.json').exists());self.assertTrue((Path(d)/'shadow/control/1-readiness.json').exists())
 def test_failed_refresh_never_reads_old_history(self):
  with tempfile.TemporaryDirectory() as d:
   with patch('qc_shadow.daily_refresh.ensure',return_value={'status':'failed','target_day':'2026-11-03'}),patch.object(worker,'Source') as source,patch.object(worker.subprocess,'run',return_value=Mock(returncode=0)):
    result=worker.run(Path(d)/'shadow',Path(d)/'production','old-history',True)
   self.assertEqual(result['status'],'failed');source.assert_not_called();self.assertFalse((Path(d)/'shadow/shadow.db').exists())
 def test_before_due_never_reads_old_history(self):
  with tempfile.TemporaryDirectory() as d:
   with patch('qc_shadow.daily_refresh.ensure',return_value={'status':'waiting_daily_refresh','target_day':'2026-11-03'}),patch.object(worker,'Source') as source:
    result=worker.run(Path(d)/'shadow',Path(d)/'production','old-history',True)
   self.assertEqual(result['status'],'waiting_daily_refresh');source.assert_not_called()
