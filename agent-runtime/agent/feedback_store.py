"""Isolated, append-only operator feedback ledger. No access to legacy decision data."""
from contextlib import contextmanager
import hashlib,json,sqlite3
from pathlib import Path


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


class Ledger:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(proposal TEXT PRIMARY KEY REFERENCES proposals(id));
            CREATE TABLE IF NOT EXISTS receipts(proposal TEXT PRIMARY KEY REFERENCES deliveries(proposal),message_id TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,message_id TEXT UNIQUE NOT NULL,proposal TEXT NOT NULL REFERENCES proposals(id),created TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checks(seq INTEGER PRIMARY KEY,proposal TEXT NOT NULL REFERENCES proposals(id),event_id TEXT NOT NULL REFERENCES events(message_id),payload TEXT NOT NULL);
            ''')
            for table in ('proposals','deliveries','receipts','events','checks'):
                for op in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable_feedback'); END")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()

    def create(self,payload):
        stable={k:v for k,v in payload.items() if k not in ('issued_at','expires_at')}
        identity='FB-'+hashlib.sha256(encoded(stable).encode()).hexdigest()[:20]
        value=dict(payload,id=identity)
        with self.db() as db:db.execute('INSERT OR IGNORE INTO proposals VALUES (?,?)',(identity,encoded(value)))
        return self.get(identity)

    def get(self,identity):
        with self.db() as db:row=db.execute('SELECT payload FROM proposals WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('建议编号不存在')
        return json.loads(row[0])

    def all(self):
        with self.db() as db:rows=db.execute('SELECT payload FROM proposals ORDER BY rowid DESC').fetchall()
        return [json.loads(row[0]) for row in rows]

    def reserve_delivery(self,identity):
        with self.db() as db:return db.execute('INSERT OR IGNORE INTO deliveries VALUES (?)',(identity,)).rowcount==1

    def finish_delivery(self,identity,message_id):
        if not message_id:raise ValueError('缺少发送回执')
        with self.db() as db:db.execute('INSERT INTO receipts VALUES (?,?)',(identity,message_id))

    def receipt(self,identity):
        with self.db() as db:row=db.execute('SELECT message_id FROM receipts WHERE proposal=?',(identity,)).fetchone()
        return row[0] if row else None

    def delivery_state(self,identity):
        if self.receipt(identity):return 'sent'
        with self.db() as db:row=db.execute('SELECT 1 FROM deliveries WHERE proposal=?',(identity,)).fetchone()
        return 'uncertain' if row else 'draft'

    def record(self,identity,event):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM events WHERE message_id=?',(event['message_id'],)).fetchone()
            if row:
                if row[0]!=encoded(event):raise ValueError('同一消息内容已变化，拒绝覆盖')
                return json.loads(row[0])
            previous=db.execute('SELECT created FROM events WHERE proposal=? ORDER BY seq DESC LIMIT 1',(identity,)).fetchone()
            if previous and event['created']<previous[0]:raise ValueError('反馈早于已保存的修订，请重新提交')
            db.execute('INSERT INTO events(message_id,proposal,created,payload) VALUES (?,?,?,?)',
                       (event['message_id'],identity,event['created'],encoded(event)))
        return event

    def events(self,identity):
        with self.db() as db:rows=db.execute('SELECT payload FROM events WHERE proposal=? ORDER BY seq',(identity,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def add_check(self,identity,event_id,result):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            latest=db.execute('SELECT message_id FROM events WHERE proposal=? ORDER BY seq DESC LIMIT 1',(identity,)).fetchone()
            if not latest or latest[0]!=event_id:raise ValueError('核验期间反馈已变化，请重新核验')
            db.execute('INSERT INTO checks(proposal,event_id,payload) VALUES (?,?,?)',(identity,event_id,encoded(result)))
        return result

    def latest_check(self,identity,event_id):
        with self.db() as db:row=db.execute('SELECT payload FROM checks WHERE proposal=? AND event_id=? ORDER BY seq DESC LIMIT 1',(identity,event_id)).fetchone()
        return json.loads(row[0]) if row else None
