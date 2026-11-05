"""The regression entry point must execute newly shipped and previously omitted tests."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class RunnerTests(unittest.TestCase):
    def test_full_runner_includes_diagnostics_deployment_and_existing_daily_checks(self):
        executed = []
        environments = []
        def run(command, **kwargs):
            executed.append(Path(command[-1]).name)
            environments.append(kwargs['env'])
            return SimpleNamespace(returncode=0)
        with patch('subprocess.run', side_effect=run), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(Path(__file__).with_name('run_all.py')), run_name='__main__')
        self.assertEqual(caught.exception.code, 0)
        for name in ('test_runtime_diagnostics.py', 'test_patch_installer.py', 'test_daily_brief.py',
                     'test_budget_report_source.py', 'test_trading_context.py'):
            self.assertIn(name, executed)
        self.assertEqual(len(executed), len(set(executed)))
        self.assertTrue(all(env.get('PYTHONUTF8') == '1' for env in environments),
                        'Child tests must read UTF-8 fixtures consistently on Windows')


if __name__ == '__main__':
    unittest.main()
