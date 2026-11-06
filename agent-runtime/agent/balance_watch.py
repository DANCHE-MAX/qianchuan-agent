"""Persistent low-balance episodes, using only the existing monitor events table."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from . import hourly as h
from .card_view import card, text_element

THRESHOLD = Decimal('500')
PREFIX = 'balance:threshold-v2:'


def observe(store, snapshot, config, now=None, covered_notice=None):
    now = now or h.now_china()
    try:
        account = str(snapshot['advertiser_id'])
        at = datetime.fromisoformat(snapshot['fetched_at'])
        if not account.isascii() or not account.isdigit() or at.tzinfo is None or now.tzinfo is None:
            raise ValueError()
        amounts = {k: Decimal(str(snapshot['amounts_yuan'][k]))
                   for k in ('account_total', 'account_valid', 'account_frozen')}
        if any(not v.is_finite() for v in amounts.values()): raise ValueError()
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError('余额阈值样本无效') from None
    # Newly created notices use a recent actual read, never a historical cached balance.
    if not -300 <= (now-at).total_seconds() <= 300:
        return None
    at = at.astimezone(h.CHINA); stamp = at.isoformat()
    level = 'low' if amounts['account_valid'] <= THRESHOLD else 'normal'
    state_key = 'balance-threshold-state-v2:'+account
    queued = None
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM events WHERE id=? AND account=?', (state_key, account)).fetchone()
        previous = json.loads(row[0]) if row else None
        if previous and (not isinstance(previous, dict) or previous.get('level') not in ('low', 'normal')):
            raise ValueError('余额阈值去重记录无效')
        if previous:
            before = datetime.fromisoformat(previous['sample_stamp'])
            if before.tzinfo is None: raise ValueError('余额阈值去重时间无效')
            if at < before or at == before and level != previous['level']:
                return None
        notice = previous.get('notice_key') if previous else None
        if level == 'normal':
            db.execute('UPDATE events SET sent=2 WHERE account=? AND sent=0 AND id LIKE ?', (account, PREFIX+account+':%'))
            # A failed fixed-hour summary may also be this episode's low notice.
            # Retire only that obsolete pending card; future summaries keep their schedule.
            if notice and notice.startswith(('balance4h:'+account+':','balance2h:'+account+':')):
                db.execute('UPDATE events SET sent=2 WHERE id=? AND account=? AND sent=0', (notice, account))
            notice = None
        elif covered_notice:
            covered = db.execute('SELECT sent FROM events WHERE id=? AND account=?', (covered_notice, account)).fetchone()
            if not covered_notice.startswith(('balance4h:'+account+':','balance2h:'+account+':')) or not covered or covered[0] == 2:
                raise ValueError('余额汇总关联无效')
            db.execute('UPDATE events SET sent=2 WHERE account=? AND sent=0 AND id LIKE ?', (account, PREFIX+account+':%'))
            notice = covered_notice
        else:
            delivered = db.execute('SELECT sent,stamp FROM events WHERE id=? AND account=?', (notice, account)).fetchone() if notice else None
            expired = bool(delivered and delivered[0] == 0 and
                           (now-datetime.fromisoformat(delivered[1])).total_seconds() > 30*60)
            if expired:
                db.execute('UPDATE events SET sent=2 WHERE id=? AND sent=0', (notice,))
            needs_notice = not previous or previous['level'] != 'low' or not delivered or delivered[0] == 2 or expired
            if needs_notice and (not previous or at > datetime.fromisoformat(previous['sample_stamp'])):
                notice = PREFIX+account+':'+hashlib.sha256(stamp.encode()).hexdigest()[:24]
                from .monitor import add_mentions
                rendered = card('千川余额｜低余额预警', [text_element(
                    '%s（北京时间）｜可用余额%.2f元，已不高于500元，请及时充值。' %
                    (at.strftime('%H:%M'), amounts['account_valid']))])
                rendered['header']['template'] = 'red'
                rendered = add_mentions(rendered, config, 'balance_alert')
                db.execute('INSERT OR IGNORE INTO events(id,account,stamp,payload) VALUES(?,?,?,?)',
                           (notice, account, stamp, json.dumps(rendered, ensure_ascii=False)))
                queued = notice
        state = {'level': level, 'sample_stamp': stamp, 'notice_key': notice}
        db.execute('INSERT OR REPLACE INTO events(id,account,stamp,payload,sent) VALUES(?,?,?,?,2)',
                   (state_key, account, stamp, json.dumps(state)))
    return queued
