"""Explicit inbound daily generation: read APIs, persist locally, reply to source only."""
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import budgets, cards, model, qianchuan, settings
from .card_view import answer_card
from .conversation import Conversation
from .notify import feishu_allowed_chat_ids
from .reliability import ServiceError

CHINA = timezone(timedelta(hours=8))
REQUEST = re.compile(r'(?:请)?(?:为我|帮我)?生成(?P<when>[\d./年月日号前昨今明天-]*?)(?:的)?(?:日报|报告)')


def request_match(command):
    return REQUEST.fullmatch(''.join(command.split()).strip('。！？!?'))


def matches(command):
    return request_match(command) is not None


def target_date(context, command='生成昨日日报'):
    # Pin to the original message's Shanghai date, even if a delivery retry crosses midnight.
    value = context.get('create_time')
    day = datetime.fromtimestamp(int(value) / 1000, CHINA).date() if value else model.china_today()
    match = request_match(command)
    if not match:
        raise ValueError('请使用“生成10.22日报”或“生成2026-10-22日报”。')
    when = match.group('when')
    if when in ('', '昨天', '昨日', '前天', '今天', '今日', '明天'):
        offset = {'前天': 2, '今天': 0, '今日': 0, '明天': -1}.get(when, 1)
        end = day - timedelta(days=offset)
    else:
        when = re.sub(r'[日号]$', '', when)
        if not re.fullmatch(r'(?:(?:\d{4}[./-])?\d{1,2}[./-]\d{1,2}|(?:\d{4}年)?\d{1,2}月\d{1,2})', when):
            raise ValueError('日期格式无法识别，请使用“生成10.22日报”或“生成2026-10-22日报”。')
        parts = [int(v) for v in re.split(r'[./年月-]', when)]
        if len(parts) == 2:
            parts.insert(0, day.year)
        try:
            end = date(*parts)
        except ValueError:
            raise ValueError('日期不存在，请检查月份、天数和闰年，例如“生成2026-10-22日报”。') from None
    if end >= min(day, model.china_today()):
        raise ValueError('解析日期为%s；只能生成已结束的完整自然日日报，不支持当天或未来日期。未写年份时按请求时的北京时间年份解析，跨年请写完整年份。' % end.isoformat())
    return end.isoformat()


def generate(root, outdir, cfg, env, end):
    """Use the same calculation/parser/card modules as CLI; no message sending here."""
    root, outdir = Path(root), Path(outdir)
    mc = dict(cfg['model'], window_days=1)
    rows, meta = qianchuan.fetch_daily(env, end, days=1)
    result = model.evaluate(rows, mc, mc['season'], end)
    active = [p for p in result['plans'] if any(p.get(k, 0) for k in ('gmv','ad_spend','backend_net','comprehensive_cost'))]
    # The model sorts by spend; cap per-request detail calls and group card volume.
    selected = active[:20]
    snapshot = budgets.fetch(env, meta['advertiser_id'], selected) if selected else None
    data_dir = root / 'data' / 'qianchuan'
    source = qianchuan.save_snapshot(rows, meta, data_dir)
    result['source_file'] = str(source.resolve())
    result['source_api'] = qianchuan.provenance(source)
    from . import data_quality
    result['data_quality'] = data_quality.daily(result, meta['advertiser_id'], end)
    result['notes'].append('数据检查：'+result['data_quality']['note'])
    result['audit']['completeness_note'] = 'API按计划/自然日读取，分页及日期校验通过；仅覆盖所选账户和乘方商品计划报表'
    result['notes'].append('千川API账户%s；读取时间%s；历史成交可能随平台归因/退款回补更新' %
                           (meta['advertiser_id'], meta['fetched_at']))
    if snapshot:
        budgets.save(snapshot, data_dir)
        budgets.attach(result, snapshot)
    result['notes'].append('群内生成只补查有经营数据且按消耗排序的前20条计划预算，卡片仅展示这些计划；完整经营记录及整体核算保留在本地报告。其他预算可用预算诊断按计划查询。')
    built = cards.build(result, active_only=True, max_plans=20)
    # Publish the report JSON last, so readers only see a complete validated report.
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.generating-', dir=outdir) as tmp:
        md = model.save(result, tmp)
        card_path = cards.save(built, md)
        publish_report(md, card_path, outdir)
    return json.loads(json.dumps(result, ensure_ascii=False, default=str)), built


def publish_report(md, card_path, outdir):
    # Keep immutable snapshots: existing reports may be open in another process.
    stem = md.stem + '_' + uuid.uuid4().hex[:12]
    targets = (stem + '.md', stem + '_cards.json', stem + '.json')
    for source, name in zip((md, card_path, md.with_suffix('.json')), targets):
        publish_file(source, Path(outdir) / name)


def publish_file(source, target):
    # mkdtemp can give files a private Windows ACL. Create the publication file
    # directly under out/ so readers in later processes inherit the out/ ACL.
    target = Path(target)
    staging = target.parent / ('.publish-' + uuid.uuid4().hex)
    try:
        with staging.open('xb') as output:
            output.write(Path(source).read_bytes())
            output.flush()
            os.fsync(output.fileno())
        os.replace(staging, target)
    finally:
        if staging.exists():
            staging.unlink()


@contextmanager
def generation_lock(path):
    """One generator per project, including accidental concurrent listener processes."""
    from .reliability import file_lock
    with file_lock(path):
        yield


def _uuid(message_id, page):
    return hashlib.sha256(('daily:' + message_id + ':' + str(page)).encode()).hexdigest()[:40]


def reply(notifier, message_id, command, root, outdir, env, context, memory=None):
    root, outdir = Path(root), Path(outdir)
    chat = context.get('chat_id')
    allowed = set(feishu_allowed_chat_ids(env) or [env.get('FEISHU_CHAT_ID')])
    if not chat or chat not in allowed or not message_id:
        # Do not send a generation response outside the configured allowlist.
        return None
    # The listener already queues work; contention across processes is retried by its inbox.
    with generation_lock(root / 'data' / 'generation.lock'):
        db = sqlite3.connect(root / 'data' / 'generation.db', timeout=10)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (message_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, target TEXT NOT NULL, payload TEXT, next_card INTEGER NOT NULL DEFAULT 0)')
            job = db.execute('SELECT chat_id,target,payload,next_card FROM jobs WHERE message_id=?', (message_id,)).fetchone()
            if job and job[0] != chat:
                raise ValueError('日报任务群不匹配')
            if not job:
                initial = None
                try:
                    end = target_date(context, command)
                except ValueError as exc:
                    end = ''
                    initial = json.dumps({'report': None, 'cards': [answer_card(str(exc), title='投流助手｜日期提示')]}, ensure_ascii=False)
                db.execute('INSERT INTO jobs(message_id,chat_id,target,payload) VALUES(?,?,?,?)', (message_id, chat, end, initial))
                db.commit()
                job = (chat, end, initial, 0)
            payload = json.loads(job[2]) if job[2] else None
            if payload is None:
                try:
                    cfg = settings.load_config(root / 'config' / 'rules.json')
                    report, built = generate(root, outdir, cfg, env, job[1])
                    payload = {'report': report, 'cards': built}
                except Exception as exc:
                    # Never reply with raw API/server errors or silently substitute an older report.
                    payload = {'report': None, 'error_type': type(exc).__name__, 'cards': [answer_card(
                        (str(exc) + '\n' if isinstance(exc, ServiceError) else '') + '%s单日日报生成失败。本次未返回旧日报代替。可能无数据或超出平台可查询范围；请核对千川授权、账户、数据完整性及本地存储，再重新发送“生成%s日报”。' % (job[1], job[1]),
                        title='投流助手｜日报生成失败')]}
                db.execute('UPDATE jobs SET payload=? WHERE message_id=?',
                           (json.dumps(payload, ensure_ascii=False), message_id))
                db.commit()
            # Each card gets a stable reply UUID; a failed page does not replay earlier pages.
            for i in range(job[3], len(payload['cards'])):
                notifier.reply_card(message_id, payload['cards'][i], request_uuid=_uuid(message_id, i))
                db.execute('UPDATE jobs SET next_card=? WHERE message_id=?', (i+1, message_id))
                db.commit()
            if payload['report']:
                report = payload['report']
                source = json.dumps({'数据窗口': report['audit'], '整体': report['totals'],
                                     '逐计划': report['plans'], '口径说明': report['notes']}, ensure_ascii=False)
                conversation = memory or Conversation(root / 'data' / 'conversation.db', account=env.get('QC_ADVERTISER_IDS'))
                conversation.save(chat, context.get('sender_id'), [{
                    'question': command, 'answer': '已生成%s单日日报并返回完整卡片。' % job[1],
                    'source': source, 'report_snapshot': report, 'display_kind': 'data'}])
            return payload['report']
        finally:
            db.close()

# health-v23
