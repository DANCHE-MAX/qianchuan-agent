import contextlib,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import feedback_cli
from agent.feedback_store import Ledger
from test_feedback_loop import CFG,ENV,spec
class CliTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'config').mkdir()
  (self.root/'config/rules.json').write_text(json.dumps(CFG));(self.root/'config/.env').write_text('\n'.join(k+'='+v for k,v in ENV.items()))
  self.path=self.root/'input.json';self.path.write_text(json.dumps(spec()));self.output=io.StringIO()
 def run_cli(self,*args,factory=None):
  with contextlib.redirect_stdout(self.output),contextlib.redirect_stderr(self.output):
   return feedback_cli.main(['--project',str(self.root),*args],notifier_factory=factory or Mock())
 def test_preview_create_persists_without_network_and_repeat_keeps_id(self):
  factory=Mock();self.assertEqual(self.run_cli('create','--file',str(self.path),factory=factory),0)
  self.assertEqual(self.run_cli('create','--file',str(self.path),factory=factory),0);factory.assert_not_called()
  rows=Ledger(self.root/'data/feedback_loop.db').all();self.assertEqual(len(rows),1)
  self.assertFalse((self.root/'data/app.db').exists());self.assertIn('未发送',self.output.getvalue())
 def test_missing_keys_fail_without_sending_or_exposing_config(self):
  self.path.write_text('{}');factory=Mock();self.assertEqual(self.run_cli('create','--file',str(self.path),factory=factory),1);factory.assert_not_called()
 def test_list_missing_file_has_no_legacy_database_side_effect(self):
  self.assertEqual(self.run_cli('list'),0);self.assertFalse((self.root/'data/app.db').exists())
if __name__=='__main__':unittest.main()
