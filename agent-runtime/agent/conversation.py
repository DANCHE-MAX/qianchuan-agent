"""Per-chat, per-user conversation snapshots with expiry."""
import json
import re
import sqlite3
import time
from pathlib import Path


def is_followup(text):
    if text.strip("？?。！! ") in ("为什么", "为什么这样", "为什么会这样"):
        return True
    return bool(re.search(r"更简单|简单点|简短点|再简短|精简|再总结|展开说|详细点|详细说|什么意思|然后呢|继续说|上面|刚才你|上一轮|换个说法|用表格|表格显示|表格展示|卡片显示|卡片展示", text))


class Conversation:
    def __init__(self, path, account=None):
        self.path = Path(path)
        self.account = str(account) if account else None

    def load(self, chat, user):
        if not chat or not user or not self.path.exists():
            return []
        db = sqlite3.connect(self.path)
        try:
            db.execute('DELETE FROM conversation WHERE updated < ?', (time.time()-1800,))
            db.commit()
            row = db.execute('SELECT payload FROM conversation WHERE chat=? AND user=?', (chat,user)).fetchone()
            turns = json.loads(row[0]) if row else []
            if self.account:
                turns = [t for t in turns if isinstance(t, dict) and t.get('advertiser_id') == self.account]
            return turns
        finally:
            db.close()

    def save(self, chat, user, turns):
        if not chat or not user:
            return
        if self.account:
            turns = [dict(t, advertiser_id=self.account) for t in turns if isinstance(t, dict)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS conversation (chat TEXT, user TEXT, updated REAL, payload TEXT, PRIMARY KEY(chat,user))')
            db.execute('DELETE FROM conversation WHERE updated < ?', (time.time()-1800,))
            db.execute('INSERT OR REPLACE INTO conversation VALUES (?,?,?,?)', (chat,user,time.time(),json.dumps(turns[-3:],ensure_ascii=False)))
            db.commit()
        finally:
            db.close()
