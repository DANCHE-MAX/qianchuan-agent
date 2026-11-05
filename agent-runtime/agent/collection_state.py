"""Persist enabled periods; restarts retain coverage, explicit pauses close a period."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from . import hourly as h


def load(root, account):
    path=Path(root)/'data/qianchuan/health.db'
    if not path.exists():return None
    with closing(sqlite3.connect(path,timeout=15)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_windows'").fetchone():return None
        return [{'start':start,'end':end} for start,end in db.execute(
            'SELECT start,stop FROM collection_windows WHERE account=? ORDER BY start',(account,))]


def sync(root,account,now,enabled):
    path=Path(root)/'data/qianchuan/health.db';path.parent.mkdir(parents=True,exist_ok=True)
    boundary=now.astimezone(h.CHINA).replace(minute=now.minute//30*30,second=0,microsecond=0)
    with closing(sqlite3.connect(path,timeout=15)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS collection_windows(account TEXT,start TEXT,stop TEXT,PRIMARY KEY(account,start))')
        db.execute('CREATE TABLE IF NOT EXISTS collection_switch(account TEXT PRIMARY KEY,enabled INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS collection_clock(account TEXT PRIMARY KEY,stamp TEXT)')
        db.commit();db.execute('BEGIN IMMEDIATE')
        try:
            previous=db.execute('SELECT stamp FROM collection_clock WHERE account=?',(account,)).fetchone()
            if previous and datetime.fromisoformat(previous[0])>now:
                db.rollback();return load(root,account)
            row=db.execute('SELECT enabled FROM collection_switch WHERE account=?',(account,)).fetchone()
            if enabled and (row is None or not row[0]):
                begin=boundary.isoformat()
                # On first upgrade only: earliest actual snapshot is evidence that collection existed.
                # Never reset this baseline on restart; process outages must remain visible.
                if row is None:
                    source=Path(root)/'data/qianchuan/monitor.db'
                    if source.exists():
                        with closing(sqlite3.connect('file:'+source.as_posix()+'?mode=ro',uri=True,timeout=15)) as other:
                            first=other.execute("SELECT MIN(bucket) FROM snapshots WHERE account=? AND kind='plan'",(account,)).fetchone()[0]
                        if first:
                            parsed=datetime.fromisoformat(first)
                            if parsed.tzinfo is None or parsed>now:raise ValueError('采集起点无效')
                            begin=parsed.astimezone(h.CHINA).isoformat()
                # An explicit resume in the same half-hour reopens that bucket, if necessary.
                db.execute('INSERT INTO collection_windows VALUES(?,?,NULL) ON CONFLICT(account,start) DO UPDATE SET stop=NULL',(account,begin))
            if not enabled:
                db.execute('UPDATE collection_windows SET stop=? WHERE account=? AND stop IS NULL',(now.isoformat(),account))
            db.execute('INSERT OR REPLACE INTO collection_switch VALUES(?,?)',(account,int(enabled)))
            db.execute('INSERT OR REPLACE INTO collection_clock VALUES(?,?)',(account,now.isoformat()))
            db.commit()
        except Exception:
            db.rollback();raise
    return load(root,account)


def classify(missing,windows):
    operational=[];historical=[];paused=[]
    for value in missing:
        at=datetime.fromisoformat(value)
        if windows and any(datetime.fromisoformat(w['start'])<=at and
                           (w['end'] is None or at<datetime.fromisoformat(w['end'])) for w in windows):
            operational.append(value)
        elif windows and at>=datetime.fromisoformat(windows[0]['start']):paused.append(value)
        elif windows is not None:historical.append(value)
    return {'operational_missing_slots':operational,'historical_missing_slots':historical,
            'paused_missing_slots':paused,'coverage_registered':windows is not None,
            'unknown_missing_slots':list(missing) if windows is None else [],
            'collection_start':windows[0]['start'] if windows else None}
