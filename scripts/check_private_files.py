"""Reject private runtime assets in the Git index; does not read secret values."""
import pathlib, subprocess, sys
names=subprocess.check_output(['git','ls-files','-z']).decode().split('\0')
blocked=[]
for name in filter(None,names):
 p=pathlib.PurePosixPath(name)
 private_dirs={'data','evidence','reports','out','outputs','_update_backups','.venv','node_modules'}
 bad=(bool(private_dirs.intersection(p.parts)) or
      (p.name.startswith('.env') and p.name!='.env.example') or
      ('config' in p.parts and p.name!='.env.example') or
      p.suffix.lower() in {'.db','.sqlite','.sqlite3','.xlsx','.xls','.csv','.zip','.pem','.key','.p12','.pfx','.log'} or
      '.bak' in p.name)
 if bad:blocked.append(name)
if blocked:
 print('Private file paths blocked:\n'+'\n'.join(blocked),file=sys.stderr);sys.exit(1)
print('Private-file index check passed.')
