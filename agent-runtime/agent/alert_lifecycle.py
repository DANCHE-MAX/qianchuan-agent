"""Local incident observations; no sender, no production action endpoints."""
import hashlib,json,sqlite3
from pathlib import Path
from datetime import datetime

def schema(db):
 db.execute('CREATE TABLE IF NOT EXISTS incident_state(key TEXT PRIMARY KEY,account TEXT,payload TEXT)')
 db.execute('CREATE TABLE IF NOT EXISTS incident_observations(id TEXT PRIMARY KEY,key TEXT,payload TEXT)')

def observe(db,o):
 schema(db)
 if o['status'] not in ('active','healthy','unknown'):raise ValueError('Invalid observation')
 at=datetime.fromisoformat(o['stamp'])
 if at.tzinfo is None:raise ValueError('Aware observation required')
 key=hashlib.sha256(json.dumps([o[k] for k in ('account','kind','subject')]).encode()).hexdigest()
 ident=hashlib.sha256((key+o['stamp']).encode()).hexdigest()
 old=db.execute('SELECT payload FROM incident_state WHERE key=?',(key,)).fetchone();old=json.loads(old[0]) if old else {}
 if old and at<=datetime.fromisoformat(old['stamp']):return old
 failures=old.get('failures',0)+1 if o['status']=='active' else 0 if o['status']=='healthy' else old.get('failures',0)
 status='recovered' if o['status']=='healthy' and old.get('failures',0)>0 else o['status']
 severity='urgent' if o['kind'] in ('funds','daily_delivery') or (o['kind']=='collection' and failures>=2) else 'attention'
 value=dict(o,key=key,id=ident,failures=failures,status=status,severity=severity,recovery_pending=(status=='recovered') or (old.get('recovery_pending',False) and o['status']=='healthy'))
 if status!='recovered' and value['recovery_pending']:value['recovery_id']=old.get('recovery_id')
 elif status=='recovered':value['recovery_id']=ident
 if o['status']=='active':value['recovery_pending']=False
 db.execute('INSERT OR IGNORE INTO incident_observations VALUES(?,?,?)',(ident,key,json.dumps(value,ensure_ascii=False)))
 db.execute('INSERT OR REPLACE INTO incident_state VALUES(?,?,?)',(key,o['account'],json.dumps(value,ensure_ascii=False)));db.commit();return value

def pending_recoveries(db,account):
 schema(db)
 return [v for row in db.execute('SELECT payload FROM incident_state WHERE account=?',(account,)) if (v:=json.loads(row[0])).get('recovery_pending')]

def ack(db,items):
 schema(db)
 for item in items:
  row=db.execute('SELECT payload FROM incident_state WHERE key=?',(item['key'],)).fetchone()
  if not row:continue
  value=json.loads(row[0])
  if value.get('recovery_id')!=item.get('recovery_id'):continue
  value['recovery_pending']=False;db.execute('UPDATE incident_state SET payload=? WHERE key=?',(json.dumps(value,ensure_ascii=False),item['key']))
 db.commit()

def record(root,account,kind,subject,stamp,status,name):
 path=Path(root)/'data/qianchuan/alert_lifecycle.db';path.parent.mkdir(parents=True,exist_ok=True)
 with sqlite3.connect(path,timeout=3) as db:return observe(db,dict(account=str(account),kind=kind,subject=str(subject),stamp=stamp,status=status,name=name))

def safe_record(*args):
 try:return record(*args)
 except (OSError,ValueError,sqlite3.Error):return None # local tracking cannot break original monitoring

def decorate(root,account,payload):
 """Only call before freezing an existing message, never create a message here."""
 from .card_view import text_element
 path=Path(root)/'data/qianchuan/alert_lifecycle.db'
 if not path.exists():return payload
 try:
  with sqlite3.connect(path,timeout=1) as db:items=pending_recoveries(db,account)
  covered=set();monitor=Path(root)/'data/qianchuan/monitor.db'
  if monitor.exists():
   with sqlite3.connect(monitor.as_uri()+'?mode=ro',uri=True,timeout=1) as db:
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('events','alert_digests'):
     if table in tables:
      for row in db.execute('SELECT payload FROM '+table+' WHERE account=? AND sent IN (0,1)',(account,)):
       covered.update(x.get('recovery_id') for x in json.loads(row[0]).get('_incident_recoveries',[]))
  items=[x for x in items if x.get('recovery_id') not in covered][:8]
 except (sqlite3.Error,OSError,ValueError):return payload
 if not items:return payload
 result=json.loads(json.dumps(payload));result['elements'].append(text_element('已恢复：'+'；'.join(x['name'] for x in items)))
 result['_incident_recoveries']=items;return result

def acknowledge(root,payload):
 items=payload.get('_incident_recoveries',[])
 if items:
  try:
   with sqlite3.connect(Path(root)/'data/qianchuan/alert_lifecycle.db',timeout=1) as db:ack(db,items)
  except (sqlite3.Error,OSError,ValueError):pass

def rule_observations(*args):
 try:return _rule_observations(*args)
 except (OSError,ValueError,sqlite3.Error):return None

def _rule_observations(root,account,kind,stamp,findings,assessment):
 """Recovery requires a complete comparable window, never mere absence of hits."""
 path=Path(root)/'data/qianchuan/alert_lifecycle.db';path.parent.mkdir(parents=True,exist_ok=True)
 with sqlite3.connect(path) as db:
  schema(db);active={str(f['id']):f for f in findings}
  prior=[json.loads(r[0]) for r in db.execute('SELECT payload FROM incident_state WHERE account=?',(account,))]
  for pid,f in active.items():observe(db,dict(account=account,kind='rule:'+kind,subject=pid,stamp=stamp,status='active',name=f.get('name') or pid,reasons=f.get('reasons',[])))
  for old in prior:
   if old['kind']!='rule:'+kind or old['subject'] in active:continue
   status='healthy' if assessment.get('state')=='ready' and old['subject'] in assessment.get('entities',{}) else 'unknown'
   observe(db,dict(account=account,kind=old['kind'],subject=old['subject'],stamp=stamp,status=status,name=old['name']))
