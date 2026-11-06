"""10:00 Asia/Shanghai: one concise card for yesterday's complete daily report.

Independent from monitoring/balance jobs. Deterministic numbers, durable retry,
explicit scope, no budget writes and no stale-report substitution.
"""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile

from . import hourly, qianchuan, model, cards, generation, settings, operation_report
from .card_view import card, fields, text_element
from .monitor import add_mentions
from .notify import feishu_allowed_chat_ids
from .reliability import file_lock
from .roi import payment_roi

DEFAULTS = {'enabled': True, 'hour': 10, 'minute': 0, 'catch_up_minutes': 120,
            'retry_seconds': 60, 'max_plans': 2, 'at_roles': [], 'failure_notice_after': 3, 'max_operations': 3}


def options(root):
    path = Path(root)/'config/daily_brief.json'
    value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    cfg = dict(DEFAULTS, **value)
    if type(cfg.get('profit_report', False)) is not bool:
        raise ValueError('利润日报开关须为布尔值')
    for name, low, high in (('hour', 0, 23), ('minute', 0, 59), ('catch_up_minutes', 1, 720),
                           ('retry_seconds', 10, 3600), ('max_plans', 1, 5), ('failure_notice_after', 1, 20), ('max_operations', 1, 10)):
        if type(cfg[name]) is not int or not low <= cfg[name] <= high:
            raise ValueError('日报配置无效：'+name)
    if type(cfg['enabled']) is not bool or not isinstance(cfg['at_roles'], list):
        raise ValueError('日报启用状态或@角色配置无效')
    return cfg


def destination(env):
    chat = (env.get('FEISHU_DAILY_BRIEF_CHAT_ID') or env.get('FEISHU_CHAT_ID') or '').strip()
    allowed = set(feishu_allowed_chat_ids(env) or [env.get('FEISHU_CHAT_ID')])
    if not chat or chat not in allowed:
        raise ValueError('日报目标群未配置或不在白名单')
    return chat


def money(value):
    return '暂无' if value is None else format(Decimal(str(value)), ',.2f')


def render(report, max_plans=2, max_operations=3):
    audit, totals = report['audit'], report['totals']
    if audit['days'] != 1 or audit['start'] != audit['end']:
        raise ValueError('重点日报只接受一个完整自然日')
    plans = report.get('plans', [])
    active = [p for p in plans if Decimal(str(p.get('gmv') or 0)) or Decimal(str(p.get('ad_spend') or 0))]
    active.sort(key=lambda p: Decimal(str(p.get('ad_spend') or 0)), reverse=True)
    elements = [text_element('数据日期：%s（北京时间完整日）｜账户%s' %
        (audit['end'], report['source_api']['advertiser_id'])),
        text_element('范围：乘方商品计划报表，不代表全部商品视频全域计划或直播。'),
        fields([('整体成交金额', money(totals.get('gmv'))+'元'),
                ('整体消耗', money(totals.get('ad_spend'))+'元'),
                ('整体支付ROI', money(payment_roi(totals))),
                ('报表涉及计划', str(len(plans))+'条（不是当前在投数）')])]
    if active:
        lines = ['重点计划（按昨日消耗，前%d条）：' % min(max_plans, len(active))]
        for p in active[:max_plans]:
            lines.append('%s（%s）：成交%s元，消耗%s元，支付ROI %s。' %
                (str(p['plan_name'])[:70], p['plan_id'], money(p.get('gmv')), money(p.get('ad_spend')), money(payment_roi(p))))
        elements.append(text_element('\n'.join(lines)))
    profit = totals.get('predicted_profit')
    elements.append(text_element('预计利润｜历史退款情景：%s。' %
        ((money(profit)+'元') if profit is not None else '必要配置不完整，未给出完整利润预测')))
    no_sales = sum(Decimal(str(p.get('ad_spend') or 0))>0 and Decimal(str(p.get('gmv') or 0))==0 for p in plans)
    loss = sum(p.get('predicted_profit') is not None and Decimal(str(p['predicted_profit']))<0 for p in plans)
    missing = sum(p.get('predicted_profit') is None for p in plans)
    attention = []
    if no_sales: attention.append('%d条计划有消耗无成交' % no_sales)
    if loss: attention.append('%d条已配置计划情景利润为负，建议核对' % loss)
    if missing: attention.append('%d条计划缺少完整利润配置' % missing)
    elements.append(text_element('需关注：'+('；'.join(attention) if attention else '未发现上述有消耗无成交或情景亏损标记；不代表没有其他风险。')))
    if report.get('data_quality'):
        elements.append(text_element('数据检查：'+report['data_quality']['note']+' 采集于'+report['data_quality']['fetched_at']))
    elements.extend(operation_report.elements(report.get('operation_report'), max_operations))
    elements.append(text_element('支付ROI＝整体成交金额÷整体消耗；零消耗时暂无。利润按现行成本及历史退款率估算，不是结算利润。本日报程序仅汇报，不执行投放操作。'))
    return card('千川投流｜昨日重点日报', elements)


def prepare(root, env, now, cfg, opts):
    if opts.get('profit_report', False):
        from . import profit_daily,two_day_daily,daily_guide
        bundle=profit_daily.prepare(root, env, now, cfg, opts)
        bundle=two_day_daily.attach(root,env,now,bundle)
        return daily_guide.attach(root,env,now,bundle)
    account = hourly.account_id(env)
    end = (now.date()-timedelta(days=1)).isoformat()
    # Shares the existing generator lock to avoid concurrent writes to source snapshots.
    with generation.generation_lock(Path(root)/'data/generation.lock'):
        rows, meta = qianchuan.fetch_daily(env, end, days=1, today=now.date())
        if str(meta.get('advertiser_id')) != account or meta.get('start') != end or meta.get('end') != end:
            raise ValueError('日报账户或日期与请求不符')
        mc = dict(cfg['model'], window_days=1)
        result = model.evaluate(rows, mc, mc['season'], end, today=now.date())
        source = qianchuan.save_snapshot(rows, meta, Path(root)/'data/qianchuan')
        result['source_file'] = str(source.resolve())
        result['source_api'] = qianchuan.provenance(source)
        result['notes'].append('定时重点日报仅展示摘要及重点计划，不查询或推断当日预算；完整逐计划数据保存在本地。')
        from . import data_quality
        result['data_quality'] = data_quality.daily(result, account, end)
        result['operation_report'] = operation_report.load(root, account, end, now)
        rendered = render(result, opts['max_plans'], opts['max_operations'])
        mention_cfg = dict(cfg)
        mention_cfg['monitoring'] = {'at_roles': {'daily_brief': opts['at_roles']}}
        rendered = add_mentions(rendered, mention_cfg, 'daily_brief')
        out = Path(root)/'out'; out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.daily-brief-', dir=out) as temp:
            md = model.save(result, temp)
            saved_card = cards.save([rendered], md)
            generation.publish_report(md, saved_card, out)
    return rendered


class Engine:
    def __init__(self, root, env, notifier_factory):
        self.root, self.env, self.notifier_factory = Path(root), env, notifier_factory
        self.path = self.root/'data/qianchuan/daily_brief.db'
        self.stopping = threading.Event()
        self.thread = None
        self.last_problem = None

    def tick(self, now=None):
        now = now or hourly.now_china()
        if now.tzinfo is None: raise ValueError('日报调度时间必须带时区')
        now = now.astimezone(hourly.CHINA)
        opts = options(self.root)
        if not opts['enabled']: return False
        due = now.replace(hour=opts['hour'], minute=opts['minute'], second=0, microsecond=0)
        if not due <= now < due+timedelta(minutes=opts['catch_up_minutes']): return False
        account, chat = hourly.account_id(self.env), destination(self.env)
        end = (now.date()-timedelta(days=1)).isoformat()
        key = 'daily-brief:'+account+':'+end
        uuid = hashlib.sha256((key+':'+chat).encode()).hexdigest()[:40]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path.with_suffix('.lock')):
            db = sqlite3.connect(self.path, timeout=10)
            try:
                db.execute('CREATE TABLE IF NOT EXISTS jobs(account TEXT,day TEXT,chat TEXT,payload TEXT,sent INTEGER DEFAULT 0,attempts INTEGER DEFAULT 0,retry_at TEXT,error_notice INTEGER DEFAULT 0,last_error TEXT,PRIMARY KEY(account,day))')
                db.execute('INSERT OR IGNORE INTO jobs(account,day,chat) VALUES(?,?,?)', (account,end,chat)); db.commit()
                row = db.execute('SELECT chat,payload,sent,attempts,retry_at,error_notice FROM jobs WHERE account=? AND day=?', (account,end)).fetchone()
                if row[2]: return False
                if row[0] != chat: raise ValueError('该日日报已绑定其他群，请人工核对，不自动更改接收者')
                if row[4] and now < datetime.fromisoformat(row[4]): return False
                rendered = json.loads(row[1]) if row[1] else None
                try:
                    if rendered is None:
                        cfg = settings.load_config(self.root/'config/rules.json')
                        rendered = prepare(self.root, self.env, now, cfg, opts)
                        db.execute('UPDATE jobs SET payload=? WHERE account=? AND day=?', (json.dumps(rendered,ensure_ascii=False),account,end)); db.commit()
                    if rendered.get('type') == 'profit_bundle':
                        from .profit_delivery import deliver
                        deliver(rendered, self.notifier_factory(chat), uuid)
                    else:
                        self.notifier_factory(chat).send_card(rendered, request_uuid=uuid)
                    db.execute('UPDATE jobs SET sent=1,last_error=NULL WHERE account=? AND day=?', (account,end)); db.commit()
                    return True
                except Exception as exc:
                    count = row[3]+1
                    db.execute('UPDATE jobs SET attempts=?,retry_at=?,last_error=? WHERE account=? AND day=?',
                        (count,(now+timedelta(seconds=opts['retry_seconds'])).isoformat(),type(exc).__name__,account,end)); db.commit()
                    print('昨日重点日报待重试：%s %s（%s）。' % (account,end,type(exc).__name__), flush=True)
                    if rendered is None and count >= opts['failure_notice_after'] and not row[5]:
                        failure = card('千川投流｜昨日重点日报暂不可用', [text_element(
                            '账户%s｜数据日期%s。日报尚未成功生成，将在本次补发窗口内重试；未使用旧日报或其他账户数据代替。余额报告独立运行。' % (account,end))])
                        self.notifier_factory(chat).send_card(failure, request_uuid=hashlib.sha256((uuid+':failure').encode()).hexdigest()[:40])
                        db.execute('UPDATE jobs SET error_notice=1 WHERE account=? AND day=?', (account,end)); db.commit()
                    return False
            finally:
                db.close()

    def work(self):
        from . import health
        while not self.stopping.is_set():
            try:
                health.run(self.root, self.env, '重点日报', self.tick); self.last_problem = None
            except Exception as exc:
                problem = type(exc).__name__
                if problem != self.last_problem: print('重点日报调度暂未完成，将重试（%s）。' % problem,flush=True)
                self.last_problem = problem
            self.stopping.wait(60)

    def start(self):
        self.thread = threading.Thread(target=self.work,name='qianchuan-daily-brief',daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread: self.thread.join(timeout=2)

# health-v23
