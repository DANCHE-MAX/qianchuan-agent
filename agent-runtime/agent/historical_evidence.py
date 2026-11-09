"""Read separate retrospective proof; never seals or rewrites sent report bundles."""
import hashlib,json
from pathlib import Path

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ledger_digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def inside(root,value):
 p=(Path(root)/value).resolve()
 if not p.is_relative_to(Path(root).resolve()):raise ValueError('Evidence outside runtime')
 return p

def load(root,account,day,ledger):
 root=Path(root).resolve();path=inside(root,Path('data/report_evidence')/account/(day+'.json'))
 if not path.exists():return None
 value=json.loads(path.read_text())
 if value.get('version')!=1 or value.get('account')!=account or value.get('date')!=day or value.get('verification')!='remote_attachments_and_excel_verified':raise ValueError('Unverified history scope')
 if value.get('ledger_sha256')!=ledger_digest(ledger):raise ValueError('History ledger changed')
 directory=inside(root,value['directory']);files=value['files']
 for name in ('result.json','delivery.json'):
  if str((directory/name).relative_to(root)) not in files:raise ValueError('Missing history binding')
 for rel,expected in files.items():
  if digest(inside(root,rel))!=expected:raise ValueError('History evidence changed')
 artifacts=[]
 for item in value['artifacts']:
  p=inside(root,item['path'])
  if not p.is_relative_to(directory) or p.suffix not in ('.png','.xlsx') or item['path'] not in files:raise ValueError('Unbound artifact')
  artifacts.append(dict(path=p,name=p.name,kind=item['kind']))
 if {x['kind'] for x in artifacts}!={'image','file'}:raise ValueError('Incomplete historical attachments')
 return dict(directory=directory,artifacts=artifacts)
