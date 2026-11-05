"""Durable fixed baselines, evidence observations and non-replayable action receipts."""
import hashlib,json,sqlite3
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path

def encoded(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def now():return datetime.now(timezone.utc).isoformat()
class ExecutionStore:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.db() as db:
   db.executescript('''
   CREATE TABLE IF NOT EXISTS baselines(account TEXT,plan TEXT,amount TEXT,live_on TEXT,evidence TEXT,PRIMARY KEY(account,plan));
   CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,account TEXT,plan TEXT,at TEXT,payload TEXT,hash TEXT);
   CREATE TABLE IF NOT EXISTS intents(key TEXT PRIMARY KEY,account TEXT,plan TEXT,at TEXT,payload TEXT);
   CREATE TABLE IF NOT EXISTS outcomes(key TEXT PRIMARY KEY REFERENCES intents(key),at TEXT,status TEXT,payload TEXT);
   ''')
   for table in ('baselines','observations','intents','outcomes'):
    for op in ('UPDATE','DELETE'):
     db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable'); END")
   for table,key in (('baselines','account=NEW.account AND plan=NEW.plan'),('observations','id=NEW.id'),('intents','key=NEW.key'),('outcomes','key=NEW.key')):
    db.execute(f"CREATE TRIGGER IF NOT EXISTS no_replace_{table} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}) BEGIN SELECT RAISE(ABORT,'immutable_key'); END")
  self.path.chmod(0o600)
 def db(self):
  db=sqlite3.connect(self.path,timeout=5);db.execute('PRAGMA foreign_keys=ON');return db
 def baseline(self,a,p):
  with self.db() as db:r=db.execute('SELECT amount,live_on,evidence FROM baselines WHERE account=? AND plan=?',(a,p)).fetchone()
  return dict(zip(('amount','live_on','evidence'),r)) if r else None
 def freeze(self,a,p,amount,live_on,evidence):
  d=Decimal(amount)
  if not d.is_finite() or d<=0 or not evidence:raise ValueError('baseline_evidence_required')
  row=(a,p,str(d),live_on,evidence)
  with self.db() as db:
   db.execute('BEGIN IMMEDIATE')
   old=db.execute('SELECT * FROM baselines WHERE account=? AND plan=?',(a,p)).fetchone()
   if old and old!=row:raise ValueError('fixed_B_cannot_change')
   if not old:db.execute('INSERT INTO baselines VALUES(?,?,?,?,?)',row)
 def observe(self,a,p,payload):
  value=encoded(payload);h=hashlib.sha256(value.encode()).hexdigest()
  with self.db() as db:db.execute('INSERT INTO observations(account,plan,at,payload,hash) VALUES(?,?,?,?,?)',(a,p,now(),value,h))
  return h
 def latest(self,a,p):
  with self.db() as db:r=db.execute('SELECT payload FROM observations WHERE account=? AND plan=? ORDER BY id DESC LIMIT 1',(a,p)).fetchone()
  return json.loads(r[0]) if r else None
 def reserve(self,key,a,p,payload):
  with self.db() as db:
   db.execute('BEGIN IMMEDIATE')
   if db.execute('SELECT 1 FROM intents WHERE key=?',(key,)).fetchone():return False
   if db.execute("SELECT 1 FROM intents i LEFT JOIN outcomes o ON i.key=o.key WHERE i.account=? AND i.plan=? AND (o.key IS NULL OR o.status='uncertain')",(a,p)).fetchone():return False
   db.execute('INSERT INTO intents VALUES(?,?,?,?,?)',(key,a,p,now(),encoded(payload)))
  return True
 def finish(self,key,status,payload):
  if status not in ('success','failed','uncertain','not_sent'):raise ValueError('invalid_outcome')
  with self.db() as db:
   if db.execute('SELECT 1 FROM outcomes WHERE key=?',(key,)).fetchone():raise ValueError('outcome_already_recorded')
   db.execute('INSERT INTO outcomes VALUES(?,?,?,?)',(key,now(),status,encoded(payload)))
 def success_count(self,a,p):
  with self.db() as db:return db.execute("SELECT count(*) FROM outcomes o JOIN intents i ON i.key=o.key WHERE i.account=? AND i.plan=? AND o.status='success'",(a,p)).fetchone()[0]
