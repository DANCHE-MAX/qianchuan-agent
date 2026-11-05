"""Persist complete replies before delivery; freeze content across listener retries."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .card_view import answer_card
from .reliability import DeliveryError


class CaptureNotifier:
    def __init__(self, notifier):
        self.notifier = notifier
        self.replies = []

    def __getattr__(self, name):
        return getattr(self.notifier, name)

    def reply_text(self, message_id, content):
        self.replies.append({'message_id': message_id, 'kind': 'text', 'content': content})
        return {}

    def reply_card(self, message_id, card, request_uuid=None):
        self.replies.append({'message_id': message_id, 'kind': 'card', 'content': card})
        return {}

    def reply_markdown(self, message_id, content):
        return self.reply_card(message_id, answer_card(content))


class Outbox:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS replies (message_id TEXT PRIMARY KEY, payload TEXT NOT NULL, next_reply INTEGER NOT NULL DEFAULT 0, first_attempt REAL, uncertain INTEGER NOT NULL DEFAULT 0)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def load(self, message_id):
        with self.db() as db:
            row = db.execute('SELECT payload,next_reply,first_attempt,uncertain FROM replies WHERE message_id=?', (message_id,)).fetchone()
        return row

    def save(self, message_id, replies):
        if any(r['message_id'] != message_id for r in replies):
            raise ValueError('回复目标消息不一致')
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO replies(message_id,payload) VALUES(?,?)',
                       (message_id, json.dumps(replies, ensure_ascii=False)))

    def deliver(self, notifier, message_id, clock=time.time):
        row = self.load(message_id)
        if row is None:
            raise ValueError('缺少已保存回复')
        replies = json.loads(row[0])
        for index in range(row[1], len(replies)):
            # Feishu UUID deduplication is time-limited. Stop uncertain old sends
            # rather than promise exactly-once or duplicate after a long outage.
            if index == row[1] and row[3] and row[2] and clock() - row[2] >= 3500:
                raise DeliveryError('上次发送结果未确认且已超出去重安全窗口，请人工核对群消息后重新提问。')
            with self.db() as db:
                db.execute('UPDATE replies SET first_attempt=COALESCE(first_attempt,?),uncertain=1 WHERE message_id=?', (clock(), message_id))
            r = replies[index]
            uid = hashlib.sha256(('reply:' + message_id + ':' + str(index)).encode()).hexdigest()[:40]
            if r['kind'] == 'card':
                notifier.reply_card(message_id, r['content'], request_uuid=uid)
            else:
                notifier.reply_text(message_id, r['content'], request_uuid=uid)
            with self.db() as db:
                db.execute('UPDATE replies SET next_reply=?,first_attempt=NULL,uncertain=0 WHERE message_id=?', (index + 1, message_id))


def dispatch(notifier, inbox, message_id, command, handler, outbox, **kwargs):
    from . import generation
    # Generation already stores the full report/cards, per-card progress and UUIDs.
    if generation.matches(command):
        return handler(notifier, message_id, command, **kwargs)
    if outbox.load(message_id) is None:
        capture = CaptureNotifier(notifier)
        handler(capture, message_id, command, **kwargs)
        outbox.save(message_id, capture.replies)
    return outbox.deliver(notifier, message_id)
