"""Immutable local evidence. Never shares the production database."""
import hashlib,json,os,sqlite3
from pathlib import Path

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
class Ledger:
    def __init__(self,path):
        self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,input TEXT NOT NULL,output TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON runs BEGIN SELECT RAISE(ABORT,'immutable');END;
            CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON runs BEGIN SELECT RAISE(ABORT,'immutable');END;''')
        os.chmod(self.path,0o600)
    def record(self,input,output):
        key=digest(input);a,b=canonical(input),canonical(output)
        with sqlite3.connect(self.path) as c:
            c.execute('INSERT OR IGNORE INTO runs VALUES(?,?,?)',(key,a,b))
            saved=c.execute('SELECT input,output FROM runs WHERE id=?',(key,)).fetchone()
            if saved!=(a,b):raise ValueError('same_input_different_result')
        return key
    def list(self):
        with sqlite3.connect(self.path) as c:return [r[0] for r in c.execute('SELECT id FROM runs ORDER BY id')]
    def get(self,key):
        with sqlite3.connect(self.path) as c:row=c.execute('SELECT input,output FROM runs WHERE id=?',(key,)).fetchone()
        if row is None:raise KeyError(key)
        return {'input':json.loads(row[0]),'output':json.loads(row[1])}
