"""Exercise the real updater on disposable cloud trees, never the live service."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/apply_runtime_patch.py'


def digest(data): return hashlib.sha256(data).hexdigest()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'Reversible patch installer is missing')
        spec = importlib.util.spec_from_file_location('runtime_patch_installer', SCRIPT)
        self.updater = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.updater)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'cloud'; self.bundle = self.root/'bundle'
        for group in ('agent', 'tests', 'tools', 'config', 'data', 'out'):
            (self.project/group).mkdir(parents=True)
        (self.project/'agent/a.py').write_bytes(b'old-a\n')
        (self.project/'agent/b.py').write_bytes(b'old-b\n')
        (self.project/'config/.env').write_bytes(b'private-credential')
        (self.project/'data/runtime.db').write_bytes(b'live-database-sentinel')
        self.manifest = {'format': 1, 'patch_id': 'runtime-test', 'baseline': [], 'files': []}
        for relative, before, after in [('agent/a.py', b'old-a\n', b'new-a\n'),
                                        ('agent/b.py', b'old-b\n', b'new-b\n'),
                                        ('agent/new.py', None, b'new-module\n')]:
            payload = self.bundle/'payload'/relative; payload.parent.mkdir(parents=True, exist_ok=True)
            payload.write_bytes(after)
            self.manifest['files'].append({'path': relative, 'before_sha256': digest(before) if before else None,
                                           'after_sha256': digest(after)})
        self.save_manifest()

    def save_manifest(self):
        (self.bundle/'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def command(self, action, *extra):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), action, '--bundle', str(self.bundle),
                                 '--project', str(self.project), *map(str, extra)], capture_output=True, text=True,
                                encoding='utf-8', timeout=30)
        return result

    def tree(self):
        return {p.relative_to(self.project).as_posix(): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}

    def test_check_is_read_only_and_apply_requires_stopped_services(self):
        original = self.tree()
        self.assertEqual(self.command('check').returncode, 0)
        self.assertEqual(self.tree(), original)
        result = self.command('apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.tree(), original)

    def test_apply_repeat_and_rollback_preserve_runtime_state(self):
        original = self.tree()
        result = self.command('apply', '--services-stopped')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((self.project/'agent/a.py').read_bytes(), b'new-a\n')
        self.assertEqual((self.project/'agent/new.py').read_bytes(), b'new-module\n')
        backups = list((self.project/'_update_backups').glob('runtime-test-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.command('apply', '--services-stopped').returncode, 0)
        self.assertEqual(len(list((self.project/'_update_backups').glob('runtime-test-*'))), 1)
        self.assertEqual(self.command('check').returncode, 0)
        result = self.command('rollback', '--services-stopped', '--backup', backups[0])
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        for path, data in original.items(): self.assertEqual((self.project/path).read_bytes(), data)
        self.assertFalse((self.project/'agent/new.py').exists())
        self.assertEqual(self.command('rollback', '--services-stopped', '--backup', backups[0]).returncode, 0)

    def test_source_conflict_does_not_write_any_other_target(self):
        (self.project/'agent/b.py').write_bytes(b'cloud-hotfix\n')
        before = self.tree()
        self.assertNotEqual(self.command('apply', '--services-stopped').returncode, 0)
        self.assertEqual(self.tree(), before)

    def test_corrupt_payload_is_rejected_before_backup_or_target_mutation(self):
        (self.bundle/'payload/agent/b.py').write_bytes(b'corrupt\n')
        before = self.tree()
        self.assertNotEqual(self.command('apply', '--services-stopped').returncode, 0)
        self.assertEqual(self.tree(), before)

    def test_changed_unchanged_dependency_blocks_apply(self):
        self.manifest['baseline'] = [{'path': 'tools/helper.py', 'sha256': digest(b'baseline\n')}]
        (self.project/'tools/helper.py').write_bytes(b'cloud-change\n'); self.save_manifest()
        before = self.tree()
        self.assertNotEqual(self.command('apply', '--services-stopped').returncode, 0)
        self.assertEqual(self.tree(), before)

    def test_protected_and_escaping_paths_are_rejected(self):
        before = self.tree()
        for relative in ('config/.env', 'data/runtime.db', '../outside.py', 'agent/../config/x.py',
                         '/tmp/x.py', 'agent\\x.py', 'tools/C:x.py'):
            with self.subTest(relative=relative):
                self.manifest['files'][0]['path'] = relative; self.save_manifest()
                self.assertNotEqual(self.command('apply', '--services-stopped').returncode, 0)
                self.assertEqual(self.tree(), before)

    def test_case_colliding_targets_are_rejected(self):
        extra = dict(self.manifest['files'][0], path='agent/A.py')
        self.manifest['files'].append(extra); self.save_manifest()
        self.assertNotEqual(self.command('check').returncode, 0)

    def test_symlink_target_is_rejected(self):
        target = self.project/'agent/a.py'; target.unlink()
        external = self.root/'outside.py'; external.write_bytes(b'old-a\n')
        try: target.symlink_to(external)
        except OSError: self.skipTest('Host cannot create symlinks; covered in cloud preflight')
        self.assertNotEqual(self.command('apply', '--services-stopped').returncode, 0)
        self.assertEqual(external.read_bytes(), b'old-a\n')

    def test_redirected_payload_root_is_rejected(self):
        # Simulate the OS symlink metadata boundary on hosts lacking symlink privileges.
        real = self.updater.redirected
        with patch.object(self.updater, 'redirected', side_effect=lambda p: p == self.bundle/'payload' or real(p)):
            with self.assertRaises(self.updater.PatchError):
                self.updater.apply(self.project, self.bundle)
        self.assertEqual((self.project/'agent/a.py').read_bytes(), b'old-a\n')

    def test_user_modified_new_file_blocks_all_rollback_writes(self):
        self.assertEqual(self.command('apply', '--services-stopped').returncode, 0)
        backup = next((self.project/'_update_backups').glob('runtime-test-*'))
        (self.project/'agent/new.py').write_bytes(b'user-added-work\n')
        before = self.tree()
        self.assertNotEqual(self.command('rollback', '--services-stopped', '--backup', backup).returncode, 0)
        self.assertEqual(self.tree(), before)

    def test_corrupt_backup_blocks_rollback_before_any_write(self):
        self.assertEqual(self.command('apply', '--services-stopped').returncode, 0)
        backup = next((self.project/'_update_backups').glob('runtime-test-*'))
        (backup/'original/agent/b.py').write_bytes(b'corrupt-backup\n')
        before = self.tree()
        self.assertNotEqual(self.command('rollback', '--services-stopped', '--backup', backup).returncode, 0)
        self.assertEqual(self.tree(), before)

    def test_failed_replace_restores_already_replaced_files(self):
        before = self.tree()
        real_write = self.updater.atomic_write
        failed = False
        def fail_second(path, data, mode=0o644):
            nonlocal failed
            if path == self.project/'agent/b.py' and not failed:
                failed = True
                raise OSError('simulated disk failure')
            return real_write(path, data, mode)
        with patch.object(self.updater, 'atomic_write', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.updater.apply(self.project, self.bundle)
        for path, data in before.items(): self.assertEqual((self.project/path).read_bytes(), data)
        self.assertFalse((self.project/'agent/new.py').exists())
        self.assertFalse((self.project/'.runtime_patch.lock').exists())

    def test_interrupted_rollback_can_resume_from_same_backup(self):
        self.assertEqual(self.command('apply', '--services-stopped').returncode, 0)
        backup = next((self.project/'_update_backups').glob('runtime-test-*'))
        code = '''
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('updater', sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
project, bundle, backup = map(Path, sys.argv[2:])
real = module.atomic_write
def interrupted(path, data, mode=0o644):
    real(path, data, mode)
    if path == project/'agent/a.py': os._exit(97)
module.atomic_write = interrupted
module.rollback(project, bundle, backup)
'''
        result = subprocess.run([sys.executable, '-c', code, str(SCRIPT), str(self.project), str(self.bundle), str(backup)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 97)
        self.assertEqual((self.project/'agent/a.py').read_bytes(), b'old-a\n')
        self.assertEqual((self.project/'agent/b.py').read_bytes(), b'new-b\n')
        # The crashed process has exited; this is the documented manual stale-lock recovery.
        (self.project/'.runtime_patch.lock').unlink()
        result = self.command('rollback', '--services-stopped', '--backup', backup)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((self.project/'agent/b.py').read_bytes(), b'old-b\n')
        self.assertFalse((self.project/'agent/new.py').exists())

    def test_malformed_manifest_shape_returns_structured_error(self):
        (self.bundle/'manifest.json').write_text('[]', encoding='utf-8')
        before = self.tree()
        result = self.command('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith('{'), 'Malformed JSON shape must produce a structured error')
        self.assertEqual(json.loads(result.stdout)['status'], 'error')
        self.assertEqual(self.tree(), before)

    def test_malformed_backup_modes_returns_structured_error_before_writes(self):
        self.assertEqual(self.command('apply', '--services-stopped').returncode, 0)
        backup = next((self.project/'_update_backups').glob('runtime-test-*'))
        path = backup/'journal.json'; journal = json.loads(path.read_text()); journal['modes'] = []
        path.write_text(json.dumps(journal))
        before = self.tree()
        result = self.command('rollback', '--services-stopped', '--backup', backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith('{'), 'Malformed journal shape must produce a structured error')
        self.assertEqual(json.loads(result.stdout)['status'], 'error')
        self.assertEqual(self.tree(), before)


if __name__ == '__main__':
    unittest.main()
