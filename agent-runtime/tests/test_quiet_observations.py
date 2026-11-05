import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import analysis_gate as g,monitor as m
from test_analysis_gate import series,NOW,ENV
class QuietTests(unittest.TestCase):
 def test_routine_and_recovery_silent_but_logged(self):
  with tempfile.TemporaryDirectory() as d:
   store=m.Store(d)
   for r in (g.assess(series()[-1:],'999','plan',NOW),g.assess(series(),'999','plan',NOW)):
    g.record(store,r,NOW,notify_observation=False)
   self.assertEqual(store.pending('999'),[])
   with store.db() as db:self.assertEqual(db.execute('select count(*) from analysis_history').fetchone()[0],2)
 def test_failure_still_notifies(self):
  with tempfile.TemporaryDirectory() as d:
   store=m.Store(d);g.record(store,g.failed('999','plan',NOW),NOW,notify_observation=False)
   self.assertEqual(len(store.pending('999')),1)
 def test_existing_queued_routine_retired_balance_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   store=m.Store(d);r=g.assess(series()[-1:],'999','plan',NOW);g.record(store,r,NOW)
   store.enqueue('balance2h:999:test','999',NOW,{'text':'balance'})
   g.record(store,r,NOW,notify_observation=False)
   self.assertEqual([x[0] for x in store.pending('999')],['balance2h:999:test'])
if __name__=='__main__':unittest.main()
