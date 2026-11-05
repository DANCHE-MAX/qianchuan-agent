"""Read-only local sampling evidence; interval and balance rates stay separate."""
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
from . import hourly as h, analysis_gate as gate, balance_cycle, settings
from .card_view import card, text_element
from .notify import feishu_allowed_chat_ids


def matches(command):
    return re.sub(r'[\s。？！!?]', '', command) in ('采集与速率', '采集速率', '采集核验')


def point_state(at, now):
    point = now.replace(minute=now.minute//30*30, second=0, microsecond=0)
    if at is not None and at.astimezone(h.CHINA).replace(minute=at.minute//30*30, second=0, microsecond=0) == point:
        return 'collected'
    return 'waiting' if (now-point).total_seconds() <= 300 else 'overdue'


def missing_kind(now, error=''):
    return {'point_state': point_state(None, now), 'record_count': None, 'bucket': None,
            'fetched_at': None, 'request_end': None, 'rows': [], 'spend_rate': None,
            'comparable_spend_rate': None, 'error': error,
            'assessment': {'state': 'blocked', 'reasons': ['samples'], 'counts': {'ready':0,'limited':0}}}


def read_kind(db, account, kind, now, stale_minutes):
    item = missing_kind(now)
    day = now.date().isoformat(); tomorrow = (now.date()+timedelta(days=1)).isoformat()
    records = db.execute('SELECT bucket,stamp,payload FROM snapshots WHERE account=? AND kind=? AND bucket>=? AND bucket<? ORDER BY bucket DESC LIMIT 3',
                         (account, kind, day+'T00:00:00+08:00', tomorrow+'T00:00:00+08:00')).fetchall()
    if not records: return item
    series = []
    for slot, fetched, payload in reversed(records):
        value = json.loads(payload)
        if not isinstance(value, dict) or value.get('bucket') != slot or value.get('fetched_at') != fetched:
            raise ValueError('Snapshot identity mismatch')
        series.append(value)
    latest = series[-1]
    parsed, error = gate.validate(latest, account, kind, now)
    if error: raise ValueError('Snapshot metadata invalid')
    item.update(bucket=latest['bucket'], fetched_at=latest['fetched_at'], request_end=latest['request_end'],
                record_count=len(latest['rows']), point_state=point_state(parsed['bucket'], now))
    assessment = gate.assess(series, account, kind, now, stale_minutes=stale_minutes)
    item['assessment'] = gate.public(assessment)
    if assessment['state'] not in ('ready', 'partial'): return item
    previous = {str(row['id']): row for row in series[-2]['rows']}
    current = {str(row['id']): row for row in latest['rows']}
    total = Decimal(0); count = 0
    for identity, entity in assessment['entities'].items():
        available = gate.usable(entity, 'ad_spend')
        delta = entity['current'].get('ad_spend') if available else None
        hours = entity['hours'][1]
        speed = delta/hours if delta is not None else None
        if speed is not None: total += speed; count += 1
        item['rows'].append({'id': identity, 'name': entity['name'],
                             'before_spend': gate.number(previous.get(identity, {}), 'ad_spend')[0],
                             'after_spend': gate.number(current.get(identity, {}), 'ad_spend')[0],
                             'start': assessment['start'], 'end': assessment['end'],
                             'minutes': str(hours*60), 'spend_delta': str(delta) if delta is not None else None,
                             'spend_rate': str(speed) if speed is not None else None,
                             'reason': None if available else gate.REASONS.get(entity['metrics']['ad_spend'], '指标不可用')})
    item['rows'].sort(key=lambda row: (row['spend_rate'] is None, -Decimal(row['spend_rate'] or '0'), row['id']))
    if count: item['comparable_spend_rate'] = str(total)
    if item['rows'] and count == len(item['rows']): item['spend_rate'] = str(total)
    return item


def read_balance(db, account, now, stale_minutes):
    result = {'point_state': point_state(None, now), 'sample': None,
              'rate': {'rate':None, 'reason':'尚无余额样本', 'window_hours':4}, 'error':''}
    row = db.execute('SELECT stamp,payload FROM balances WHERE account=? ORDER BY stamp DESC LIMIT 1', (account,)).fetchone()
    if not row: return result
    value = json.loads(row[1])
    at = balance_cycle.stamp(value['fetched_at'])
    amounts = {key: Decimal(str(value['amounts_yuan'][key])) for key in ('account_total','account_valid','account_frozen')}
    if str(value['advertiser_id']) != account or row[0] != value['fetched_at'] or any(not v.is_finite() for v in amounts.values()) or at > now+timedelta(minutes=5):
        raise ValueError('Balance metadata invalid')
    result.update(sample={'advertiser_id':account, 'fetched_at':value['fetched_at'],
                          'amounts_yuan':{k:str(v) for k,v in amounts.items()}}, point_state=point_state(at, now))
    if (now-at).total_seconds() > stale_minutes*60:
        result['rate']['reason'] = '余额样本已过期，暂不提供当前四小时速率'
        return result
    rows = db.execute('SELECT payload FROM balances WHERE account=? AND stamp>=? AND stamp<=? ORDER BY stamp',
                      (account, (at-timedelta(hours=5)).isoformat(), value['fetched_at'])).fetchall()
    result['rate'] = balance_cycle.rate([json.loads(r[0]) for r in rows], value, hours=4)
    return result


def inspect(root, env, now=None):
    now = now or h.now_china()
    if now.tzinfo is None: raise ValueError('查询时间须带时区')
    now = now.astimezone(h.CHINA); account = h.account_id(env)
    from .health import options
    stale = options(root)['snapshot_stale_minutes']
    status = {'account':account, 'checked_at':now.isoformat(),
              'point':now.replace(minute=now.minute//30*30,second=0,microsecond=0).isoformat(),
              'plan':missing_kind(now), 'material':missing_kind(now),
              'balance':{'point_state':point_state(None,now), 'sample':None, 'error':'',
                         'rate':{'rate':None,'reason':'尚无余额样本','window_hours':4}}}
    kinds=settings.configured_collection_kinds(root,account)
    for kind in ('plan','material'):
        if kind not in kinds:status[kind].update(point_state='not_applicable',error='')
    path = Path(root)/'data/qianchuan/monitor.db'
    if not path.is_file():
        for kind in (*kinds,'balance'): status[kind]['error']='本地采集库尚不存在'
        return status
    errors = (sqlite3.Error, OSError, ValueError, TypeError, KeyError, ArithmeticError)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as db:
            db.execute('BEGIN')
            for kind in kinds:
                try: status[kind]=read_kind(db,account,kind,now,stale)
                except errors: status[kind]['error']='本地快照读取或校验失败，速率不可用'
            try: status['balance']=read_balance(db,account,now,stale)
            except errors:
                status['balance']['error']='本地余额记录读取或校验失败'
                status['balance']['rate']['reason']='余额记录读取或校验失败，速率不可用'
    except errors:
        for kind in (*kinds,'balance'): status[kind]['error']='本地采集库读取失败'
    return status


def money(value):
    try:
        amount = Decimal(str(value))
        return format(amount, '.2f') if amount.is_finite() else '不可用'
    except (ValueError, TypeError, ArithmeticError):
        return '不可用'


def clock(value):
    return balance_cycle.stamp(value).strftime('%m-%d %H:%M:%S') if value else '暂无'


def sections(status):
    labels = {'collected':'本观察点已落库', 'waiting':'本观察点待采（前5分钟）', 'overdue':'本观察点尚未落库，已超过5分钟'}
    lines = ['账户%s｜核验时间%s（北京时间）' % (status['account'],clock(status['checked_at'])),
             '当前观察点：%s。本查询读取本地记录，不触发重新采集。' % clock(status['point'])]
    for kind, title in (('plan','计划｜乘方商品报表'),('material','素材｜商品视频累计报表')):
        item = status[kind]
        if item.get('point_state')=='not_applicable':continue
        lines.append(title+'：'+labels[item['point_state']])
        if item['error']:
            lines.append(item['error']); continue
        if item['record_count'] is None:
            lines.append('暂无已保存快照，区间速率不可用。'); continue
        lines.append('最新观察点%s｜采集%s｜数据截至%s｜%d条记录。' %
                     (clock(item['bucket']),clock(item['fetched_at']),clock(item['request_end']),item['record_count']))
        if item['record_count']==0: lines.append('空列表已入库；不能据此确认没有投放或没有消耗。')
        lines.append(gate.describe(item['assessment']))
        if item['spend_rate'] is not None:
            lines.append('该类可比对象合计消耗速率：%s元/小时。' % money(item['spend_rate']))
        elif item['comparable_spend_rate'] is not None:
            lines.append('仅可计算对象的消耗速率小计：%s元/小时，未包含受限对象。' % money(item['comparable_spend_rate']))
        else: lines.append('区间消耗速率：不可用，未以0代替。')
        for row in item['rows'][:20]:
            lines.append('%s（%s）｜%s—%s｜累计%s→%s元｜差额%s元｜间隔%s分钟｜速率%s元/小时%s' %
                         (row['name'][:80],row['id'][:32],clock(row['start']),clock(row['end']),
                          money(row['before_spend']),money(row['after_spend']),money(row['spend_delta']),
                          money(row['minutes']),money(row['spend_rate']),('｜'+row['reason']) if row['reason'] else ''))
        if len(item['rows'])>20: lines.append('仅展示前20个对象；合计包含全部可计算对象。')
    value = status['balance']; sample = value['sample']; rate = value['rate']
    lines.append('余额：'+labels[value['point_state']])
    if value['error']: lines.append(value['error'])
    if sample:
        amounts=sample['amounts_yuan']
        lines.append('余额采集于%s｜总余额%s元｜可用%s元｜冻结%s元。' %
                     (clock(sample['fetched_at']),money(amounts['account_total']),money(amounts['account_valid']),money(amounts['account_frozen'])))
    if rate['rate'] is None: lines.append('近四小时余额净下降速率：无法计算；'+rate['reason']+'。')
    else: lines.append('余额计算窗口%s—%s｜近四小时余额净下降速率：%s元/小时。' % (clock(rate['start']),clock(rate['end']),money(rate['rate'])))
    lines.append('采集范围内的速率来自累计快照差分，可能含归因回补；余额速率来自账户总余额净下降，两者分别核对。')
    return lines


def rendered(status):
    return card('投流助手｜采集与速率', [text_element(line) for line in sections(status)])


def reply(notifier, message_id, root, env, context):
    allowed=set(feishu_allowed_chat_ids(env) or [env.get('FEISHU_CHAT_ID')])
    if not message_id or not context.get('chat_id') or context['chat_id'] not in allowed: return None
    return notifier.reply_card(message_id, rendered(inspect(root,env)))
