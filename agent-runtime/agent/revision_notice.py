"""Durable correction identities, independent of changing analysis readiness."""
import hashlib
import json
from decimal import Decimal
from .card_view import card, text_element


def facts(result):
    for pid, entity in sorted(result.get('entities', {}).items()):
        for change in entity.get('revisions', []):
            identity = [str(result['account']), result['kind'], str(pid), change['metric'],
                        change['start'], change['end'],
                        str(Decimal(change['before']).normalize()), str(Decimal(change['after']).normalize())]
            key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            yield key, pid, entity, change


def enqueue(db, result, now):
    from .analysis_gate import revision_details
    db.execute('CREATE TABLE IF NOT EXISTS revision_notices(fact TEXT PRIMARY KEY,event TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS revision_migrations(account TEXT,kind TEXT,PRIMARY KEY(account,kind))')
    account, kind = result['account'], result['kind']
    if not db.execute('SELECT 1 FROM revision_migrations WHERE account=? AND kind=?',(account,kind)).fetchone():
        # Match saved assessments to old delivered or uncertain/pending outbox entries.
        # Never turn an old unknown delivery into a new send with a different UUID.
        for event, payload in db.execute('SELECT e.id,h.payload FROM events e JOIN analysis_history h ON e.account=h.account AND e.stamp=h.stamp WHERE h.account=? AND h.kind=? AND e.id LIKE ? AND e.sent IN (0,1)',(account,kind,'analysis-status:'+account+':'+kind+':%')).fetchall():
            for key, *_ in facts(json.loads(payload)):
                db.execute('INSERT OR IGNORE INTO revision_notices VALUES(?,?)',(key,event))
        db.execute('INSERT INTO revision_migrations VALUES(?,?)',(account,kind))
    fresh = [f for f in facts(result) if not db.execute('SELECT 1 FROM revision_notices WHERE fact=?',(f[0],)).fetchone()]
    for offset in range(0,len(fresh),8):
        batch=fresh[offset:offset+8]
        event='revision-v1:'+account+':'+hashlib.sha256('|'.join(sorted(f[0] for f in batch)).encode()).hexdigest()[:40]
        selected=dict(result,entities={})
        for key,pid,entity,change in batch:
            selected['entities'].setdefault(pid,dict(entity,revisions=[]))['revisions'].append(change)
        rendered=card('投流分析｜指标回调提示', [text_element(x) for x in revision_details(selected)]+[text_element('按后台累计指标变化识别，不代表利润损失；未调整投放。')])
        db.execute('INSERT OR IGNORE INTO events(id,account,stamp,payload) VALUES(?,?,?,?)',(event,account,now.isoformat(),json.dumps(rendered,ensure_ascii=False)))
        db.executemany('INSERT INTO revision_notices VALUES(?,?)',[(f[0],event) for f in batch])
