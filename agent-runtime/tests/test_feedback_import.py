import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.store import Store
class FeedbackImportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.s=Store(str(Path(self.tmp.name)/'app.db'));self.addCleanup(self.s.conn.close);self.s.init_schema()
  self.s.conn.execute("INSERT INTO decision(decision_id,adopted,adopt_note,final_budget,result_2h) VALUES ('D1','adopted','original',100,2)");self.s.conn.commit()
 def row(self):return dict(self.s.conn.execute("SELECT * FROM decision WHERE decision_id='D1'").fetchone())
 def test_partial_update_keeps_blank_and_missing_values(self):
  r=self.s.apply_feedback([{'decision_id':'D1','adopt_note':'new','adopted':'','final_budget':None,'result_2h':' '}])
  self.assertEqual(r['updated'],1);self.assertEqual(self.row()['adopted'],'adopted');self.assertEqual(self.row()['final_budget'],100);self.assertEqual(self.row()['result_2h'],2)
  before,after=self.s.conn.execute('SELECT before_json,after_json FROM feedback_import_audit').fetchone()
  self.assertEqual(json.loads(before)['adopt_note'],'original');self.assertEqual(json.loads(after)['adopt_note'],'new')
 def test_count_only_changed_matches_and_report_unknown(self):
  r=self.s.apply_feedback([{'decision_id':'missing','adopted':'adopted'},{'decision_id':'D1','adopted':'adopted'},{'decision_id':'D1','final_budget':'0'}])
  self.assertEqual((r['updated'],r['unmatched'],r['unchanged'],r['invalid']),(1,1,1,0));self.assertEqual(self.row()['final_budget'],0)
  self.assertEqual(r['details'][0]['status'],'unmatched')
 def test_invalid_row_never_partially_updates(self):
  for value in ['NaN','Infinity','oops','-1']:
   r=self.s.apply_feedback([{'decision_id':'D1','adopt_note':'bad','final_budget':value}]);self.assertEqual(r['invalid'],1);self.assertEqual(self.row()['adopt_note'],'original')
  self.assertEqual(self.s.apply_feedback([{'adopted':'adopted'},{'decision_id':'D1','adopted':'anything'}])['invalid'],2)
 def test_repeat_does_not_create_another_revision(self):
  row={'decision_id':'D1','adopt_note':'updated'}
  self.s.apply_feedback([row]);self.assertEqual(self.s.apply_feedback([row])['unchanged'],1)
  self.assertEqual(self.s.conn.execute('SELECT count(*) FROM feedback_import_audit').fetchone()[0],1)
if __name__=='__main__':unittest.main()
