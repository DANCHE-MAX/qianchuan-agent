"""Offline, source-only updater. No service control, credentials or network access."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import uuid


class PatchError(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def redirected(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def inside(root, relative):
    if redirected(root):
        raise PatchError('Symlink/junction root rejected')
    parts = PurePosixPath(relative).parts
    if not parts or '\\' in relative or ':' in relative or relative.startswith('/') or any(p in ('.', '..') for p in parts):
        raise PatchError('Unsafe path')
    path = root
    for part in parts:
        path = path/part
        if redirected(path):
            raise PatchError('Symlink/junction rejected: '+relative)
    if not path.resolve().is_relative_to(root.resolve()):
        raise PatchError('Path escapes root')
    return path


def source_path(root, relative):
    if not isinstance(relative, str) or not re.fullmatch(r'(?:agent|tests|tools)/[A-Za-z_][A-Za-z_0-9]*\.py|main\.py', relative):
        raise PatchError('Only source files are allowed')
    return inside(root, relative)


def root_path(value):
    path = Path(value).absolute()
    if redirected(path) or not path.is_dir():
        raise PatchError('Expected an existing real directory')
    return path.resolve()


def file_hash(path):
    if not path.exists():
        return None
    if not path.is_file():
        raise PatchError('Expected a regular source file')
    return sha(path.read_bytes())


def valid_hash(value, nullable=False):
    return (nullable and value is None) or isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def load_bundle(bundle):
    bundle = root_path(bundle)
    raw = inside(bundle, 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise PatchError('Manifest must be an object')
    if type(manifest.get('format')) is not int or manifest['format'] != 1 or not isinstance(manifest.get('patch_id'), str) or not re.fullmatch('[a-zA-Z0-9_-]{1,80}', manifest['patch_id']):
        raise PatchError('Unsupported manifest')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries:
        raise PatchError('Empty manifest')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PatchError('Manifest entries must be objects')
        relative = entry.get('path')
        source_path(bundle/'payload', relative)
        if relative.casefold() in seen:
            raise PatchError('Duplicate/case-colliding target')
        seen.add(relative.casefold())
        if not valid_hash(entry.get('before_sha256'), True) or not valid_hash(entry.get('after_sha256')) or entry['before_sha256'] == entry['after_sha256']:
            raise PatchError('Invalid before/after hash')
        if file_hash(source_path(bundle/'payload', relative)) != entry['after_sha256']:
            raise PatchError('Payload hash mismatch: '+relative)
    baseline_seen = set()
    baseline = manifest.get('baseline', [])
    if not isinstance(baseline, list):
        raise PatchError('Baseline must be a list')
    for entry in baseline:
        if not isinstance(entry, dict):
            raise PatchError('Baseline entries must be objects')
        relative = entry.get('path')
        source_path(bundle, relative)
        if relative.casefold() in baseline_seen or not valid_hash(entry.get('sha256')):
            raise PatchError('Invalid baseline')
        baseline_seen.add(relative.casefold())
    return bundle, manifest, sha(raw)


def inspect_project(project, manifest):
    project = root_path(project)
    changed = {e['path']: e for e in manifest['files']}
    for entry in manifest.get('baseline', []):
        if entry['path'] not in changed and file_hash(source_path(project, entry['path'])) != entry['sha256']:
            raise PatchError('Baseline dependency changed: '+entry['path'])
    states = []
    for entry in manifest['files']:
        path = source_path(project, entry['path'])
        if not path.parent.is_dir():
            raise PatchError('Missing source directory: '+entry['path'])
        current = file_hash(path)
        if current == entry['after_sha256']:
            states.append('applied')
        elif current == entry['before_sha256']:
            states.append('baseline')
        else:
            raise PatchError('Source conflict: '+entry['path'])
    if len(set(states)) != 1:
        raise PatchError('Mixed patch state; use the existing backup to recover')
    return states[0]


def atomic_write(path, data, mode=0o644):
    """Atomic file replacement; callers validate all destinations beforehand."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.runtime-patch-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save_journal(backup, journal):
    atomic_write(inside(backup, 'journal.json'), json.dumps(journal, indent=2).encode('utf-8'), 0o600)


@contextmanager
def lock(project):
    path = inside(project, '.runtime_patch.lock')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise PatchError('Updater lock exists; confirm no updater is running before manual recovery') from None
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def restore_files(project, contents, modes):
    for relative, data in contents.items():
        destination = source_path(project, relative)
        if data is None:
            if destination.exists(): destination.unlink()
        else:
            atomic_write(destination, data, modes[relative])


def apply(project, bundle):
    project = root_path(project)
    bundle, manifest, manifest_hash = load_bundle(bundle)
    state = inspect_project(project, manifest)
    if state == 'applied':
        return {'status': 'already_applied', 'patch_id': manifest['patch_id']}
    with lock(project):
        inspect_project(project, manifest)
        # Read the entire verified payload and original sources before writing targets.
        payload = {e['path']: source_path(bundle/'payload', e['path']).read_bytes() for e in manifest['files']}
        originals, modes = {}, {}
        for entry in manifest['files']:
            relative = entry['path']; path = source_path(project, relative)
            originals[relative] = path.read_bytes() if path.exists() else None
            modes[relative] = stat.S_IMODE(path.stat().st_mode) & 0o777 if path.exists() else 0o644
            if sha(payload[relative]) != entry['after_sha256'] or (sha(originals[relative]) if originals[relative] is not None else None) != entry['before_sha256']:
                raise PatchError('File changed during preflight: '+relative)
        backup_parent = inside(project, '_update_backups'); backup_parent.mkdir(exist_ok=True)
        name = manifest['patch_id']+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        backup = inside(backup_parent, name); backup.mkdir(mode=0o700)
        for relative, data in originals.items():
            if data is not None:
                path = inside(backup, 'original/'+relative); path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(path, data, 0o600)
        journal = {'project': str(project), 'manifest_sha256': manifest_hash, 'state': 'applying', 'modes': modes}
        save_journal(backup, journal)
        print(json.dumps({'status': 'backup_created', 'backup': str(backup)}), flush=True)
        try:
            for relative, data in payload.items():
                atomic_write(source_path(project, relative), data, modes[relative])
            if inspect_project(project, manifest) != 'applied':
                raise PatchError('Post-update verification failed')
            journal['state'] = 'applied'; save_journal(backup, journal)
        except BaseException:
            try:
                restore_files(project, originals, modes)
                journal['state'] = 'recovered'; save_journal(backup, journal)
            except BaseException:
                journal['state'] = 'recovery_failed'; save_journal(backup, journal)
                raise PatchError('Update and automatic recovery failed; keep services stopped; backup: '+str(backup))
            raise
    return {'status': 'applied', 'patch_id': manifest['patch_id'], 'backup': str(backup)}


def rollback(project, bundle, backup):
    project = root_path(project)
    bundle, manifest, manifest_hash = load_bundle(bundle)
    backup_parent = inside(project, '_update_backups')
    backup = Path(backup).absolute()
    if backup.parent != backup_parent or redirected(backup):
        raise PatchError('Backup must be a real direct child of project/_update_backups')
    backup = root_path(backup)
    journal = json.loads(inside(backup, 'journal.json').read_bytes())
    if not isinstance(journal, dict) or not isinstance(journal.get('modes'), dict):
        raise PatchError('Invalid backup journal shape')
    if journal.get('project') != str(project) or journal.get('manifest_sha256') != manifest_hash:
        raise PatchError('Backup does not belong to this project and bundle')
    if journal.get('state') not in {'applying', 'applied', 'rolling_back', 'rolled_back', 'recovered', 'recovery_failed'}:
        raise PatchError('Unknown backup state')
    originals, modes = {}, {}
    for entry in manifest['files']:
        relative = entry['path']; before = entry['before_sha256']
        original = inside(backup, 'original/'+relative)
        originals[relative] = original.read_bytes() if before is not None else None
        if before is not None and sha(originals[relative]) != before:
            raise PatchError('Backup content mismatch: '+relative)
        mode = journal.get('modes', {}).get(relative)
        if type(mode) is not int or not 0 <= mode <= 0o777:
            raise PatchError('Invalid backup permissions')
        modes[relative] = mode

    def verify_current():
        contents, current_modes = {}, {}
        for entry in manifest['files']:
            relative = entry['path']; path = source_path(project, relative)
            actual = file_hash(path)
            if journal['state'] == 'applied': expected = {entry['after_sha256']}
            elif journal['state'] in ('rolled_back', 'recovered'): expected = {entry['before_sha256']}
            else: expected = {entry['before_sha256'], entry['after_sha256']}
            if actual not in expected:
                raise PatchError('Rollback conflict: '+relative)
            contents[relative] = path.read_bytes() if path.exists() else None
            current_modes[relative] = stat.S_IMODE(path.stat().st_mode) & 0o777 if path.exists() else 0o644
        return contents, current_modes

    verify_current()
    if journal['state'] == 'rolled_back':
        return {'status': 'already_rolled_back', 'backup': str(backup)}
    with lock(project):
        current, current_modes = verify_current()
        previous_state = journal['state']
        journal['state'] = 'rolling_back'; save_journal(backup, journal)
        try:
            restore_files(project, originals, modes)
            for entry in manifest['files']:
                if file_hash(source_path(project, entry['path'])) != entry['before_sha256']:
                    raise PatchError('Post-rollback verification failed')
            journal['state'] = 'rolled_back'; save_journal(backup, journal)
        except BaseException:
            try:
                restore_files(project, current, current_modes)
                journal['state'] = previous_state; save_journal(backup, journal)
            except BaseException:
                journal['state'] = 'recovery_failed'; save_journal(backup, journal)
                raise PatchError('Rollback recovery failed; keep services stopped; backup: '+str(backup))
            raise
    return {'status': 'rolled_back', 'backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'apply', 'rollback'))
    parser.add_argument('--project', required=True)
    parser.add_argument('--bundle', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--services-stopped', action='store_true', help='Operator confirms listener and health loop are stopped')
    parser.add_argument('--backup')
    args = parser.parse_args()
    try:
        if args.action != 'check' and not args.services_stopped:
            raise PatchError('Stop listener and health loop, then pass --services-stopped')
        if args.action == 'check':
            _, manifest, _ = load_bundle(args.bundle)
            result = {'status': inspect_project(args.project, manifest), 'patch_id': manifest['patch_id']}
        elif args.action == 'apply':
            result = apply(args.project, args.bundle)
        else:
            if not args.backup: raise PatchError('Rollback requires --backup')
            result = rollback(args.project, args.bundle, args.backup)
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
