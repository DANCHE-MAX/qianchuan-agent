import unittest,tempfile,importlib.util
from pathlib import Path
from datetime import datetime
from unittest.mock import patch,MagicMock
spec=importlib.util.spec_from_file_location('continuous_test',Path(__file__).resolve().parents[1]/'tools/continuous_shadow.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
class ClockTests(unittest.TestCase):
 def test_snapshot_clock_is_after_daily_acquisition(self):
  a=datetime.fromisoformat('2026-11-04T09:45:00+08:00');b=datetime.fromisoformat('2026-11-04T09:46:00+08:00');src=MagicMock();src.accounts.return_value=['1'];src.snapshot.side_effect=RuntimeError('stop_before_any_writes')
  with tempfile.TemporaryDirectory() as t,patch.object(worker,'datetime') as clock,patch.object(worker,'Source',return_value=src),patch('qc_shadow.daily_refresh.ensure',return_value={'status':'ok','history':'unused'}):
   clock.now.side_effect=[a,b]
   with self.assertRaises(RuntimeError):worker.run(Path(t)/'home',Path(t)/'source','seed',True)
   self.assertEqual(src.snapshot.call_args.args[1],b.isoformat())
