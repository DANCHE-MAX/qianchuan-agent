"""Freeze a compact outbox digest; retain original events and cooldowns."""
import hashlib,json
from datetime import datetime
from .card_view import card,text_element


def deliver(store,account,chat,now,notifier,allowed):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS alert_digests(id TEXT PRIMARY KEY,account TEXT,chat TEXT,stamp TEXT,members TEXT,payload TEXT,sent INTEGER DEFAULT 0)')
        records=db.execute('SELECT id,chat,stamp,members,payload,sent FROM alert_digests WHERE account=?',(account,)).fetchall()
    covered={key for row in records for key in json.loads(row[3])}
    groups={}
    for key,payload,created in store.pending(account):
        if key in covered or not key.startswith('anomaly26:') or not allowed(key):continue
        info=json.loads(payload).get('_brief_alert')
        if not info or (now-datetime.fromisoformat(created)).total_seconds()>1800:continue
        group=(info['kind'],info['window'])
        groups.setdefault(group,[]).append((key,json.loads(payload),created))
    for group,members in groups.items():
        # Keep cards within platform size limits without silently dropping findings.
        for offset in range(0,len(members),8):
            chunk=sorted(members,key=lambda row:(row[1]['_brief_alert'].get('priority',1),row[0]))[offset:offset+8]
            keys=[r[0] for r in chunk];ident=hashlib.sha256((chat+':'+json.dumps(keys)).encode()).hexdigest()[:40]
            elements=[text_element(group[1]+'｜同轮异常合并；各计划分别判断')]
            elements.extend(text_element(r[1]['_brief_alert']['text']) for r in chunk)
            mentions=[]
            for _,payload,_ in chunk:
                for element in payload.get('elements',[]):
                    if '<at ' in element.get('text',{}).get('content','') and element not in mentions:mentions.append(element)
            rendered=card('投流监测｜异常汇总',elements+[text_element('按快照观测变化提醒，不代表利润损失；只供人工判断，不自动调预算。')]+mentions)
            rendered['header']['template']='orange'
            from . import alert_lifecycle
            rendered=alert_lifecycle.decorate(store.path.parent.parent.parent,account,rendered)
            row=(ident,chat,min(r[2] for r in chunk),json.dumps(keys),json.dumps(rendered,ensure_ascii=False),0)
            with store.db() as db:db.execute('INSERT OR IGNORE INTO alert_digests VALUES(?,?,?,?,?,?,?)',(ident,account,*row[1:]))
            records.append(row);covered.update(keys)
    for ident,target,created,members,payload,sent in records:
        if sent:continue
        keys=json.loads(members)
        if target!=chat:continue  # Never redirect an already frozen delivery.
        if (now-datetime.fromisoformat(created)).total_seconds()>1800:
            with store.db() as db:
                db.execute('UPDATE alert_digests SET sent=2 WHERE id=?',(ident,))
                db.executemany('UPDATE events SET sent=2 WHERE id=? AND sent=0',[(k,) for k in keys])
            continue
        if not all(allowed(k) for k in keys):continue
        # Stable UUID survives transport failure / a restart. No alternative sender.
        notifier.send_card(json.loads(payload),request_uuid=ident)
        with store.db() as db:
            db.execute('UPDATE alert_digests SET sent=1 WHERE id=?',(ident,))
            db.executemany('UPDATE events SET sent=1 WHERE id=? AND sent=0',[(k,) for k in keys])
        from . import alert_lifecycle
        alert_lifecycle.acknowledge(store.path.parent.parent.parent,json.loads(payload))
    return covered
