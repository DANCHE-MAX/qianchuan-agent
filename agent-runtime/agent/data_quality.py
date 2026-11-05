"""Local evidence checks; completeness is scoped to the requested API topic."""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from . import hourly as h


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('数据时间缺少时区')
    return result.astimezone(h.CHINA)


def daily(report, account, day):
    source, audit = report.get('source_api') or {}, report['audit']
    if (str(source.get('advertiser_id')) != str(account) or
        source.get('start') != day or source.get('end') != day or
        audit['start'] != day or audit['end'] != day or audit['days'] != 1 or
        source.get('data_topic') != h.q.TOPIC or source.get('monetary_unit') != 'CNY'):
        raise ValueError('日报账户、日期、范围或单位校验失败')
    seen = set()
    for plan in report['plans']:
        pid = str(plan['plan_id'])
        if not pid.isascii() or not pid.isdigit() or pid in seen:
            raise ValueError('日报计划重复或ID无效')
        seen.add(pid)
    for key in h.FIELDS:
        values = [Decimal(str(p[key])) for p in report['plans']]
        total = Decimal(str(report['totals'][key]))
        if (not total.is_finite() or any(not v.is_finite() or v < 0 for v in values)
                or sum(values, Decimal(0)) != total):
            raise ValueError('日报明细与汇总不一致或金额无效')
    if not seen:
        raise ValueError('日报为空，不能当作零成交日报')
    fetched = stamp(source['fetched_at'])
    if fetched.date().isoformat() <= day:
        raise ValueError('日报采集时间早于完整自然日结束')
    return {'state': 'checked', 'account': str(account), 'date': day,
            'fetched_at': source['fetched_at'], 'plan_count': len(seen),
            'note': '已核对账户、日期、单位、计划唯一性及明细汇总；接口分页由采集器校验。仅乘方商品报表，不证明全账户覆盖；历史数据仍可能回补。'}


def current(root, env, now=None, windows=None, stale_minutes=45):
    """Empty successful hours differ from absent hours. Never fill gaps with zero."""
    from . import monitor
    now = (now or h.now_china()).astimezone(h.CHINA)
    account = h.account_id(env); day = now.date().isoformat()
    hs = h.Store(root); ms = monitor.Store(root)
    hours = hs.hours(account, day) if hs.path.exists() else []
    expected = [now.replace(hour=i, minute=0, second=0, microsecond=0) for i in range(now.hour)]
    covered = set(); invalid = 0
    for value in hours:
        try:
            start, end = stamp(value['start']), stamp(value['end'])
            if (str(value['advertiser_id']) != account or start not in expected or
                    end != start+timedelta(hours=1) or stamp(value['fetched_at']) > now+timedelta(minutes=5)):
                raise ValueError()
            seen = set()
            for row in value['rows']:
                if row['plan_id'] in seen: raise ValueError()
                seen.add(row['plan_id'])
                for key in h.FIELDS:
                    number = Decimal(str(row[key]))
                    if not number.is_finite() or number < 0: raise ValueError()
            covered.add(start)
        except (KeyError, ValueError, TypeError, ArithmeticError): invalid += 1
    hour_invalid = invalid
    snapshots = ms.snapshots(account, 'plan', day) if ms.path.exists() else []
    latest = snapshots[-1] if snapshots else None
    cutoff = None; age = None
    if latest:
        try:
            cutoff = stamp(latest.get('request_end') or latest['fetched_at'])
            if str(latest['account']) != account or latest['day'] != day or cutoff > now+timedelta(minutes=5):
                raise ValueError()
            seen = set()
            for row in latest['rows']:
                pid = str(row['id'])
                if not pid.isascii() or not pid.isdigit() or pid in seen: raise ValueError()
                seen.add(pid)
                for key in monitor.METRICS:
                    number = Decimal(str(row[key]))
                    if not number.is_finite() or number < 0: raise ValueError()
            age = max(0, (now-cutoff).total_seconds()/60)
        except (KeyError, ValueError, TypeError, ArithmeticError): invalid += 1; cutoff = None
    # Missing snapshots affect interval analysis, not the latest cumulative totals' day coverage.
    slots = {x['bucket'] for x in snapshots}
    expected_slots = [now.replace(hour=i//2, minute=(i%2)*30, second=0, microsecond=0).isoformat()
                      for i in range(now.hour*2 + now.minute//30)]
    missing = [x.strftime('%H:%M') for x in expected if x not in covered]
    notes = []
    if missing: notes.append('完整小时缺失%d个，不以0填补' % len(missing))
    if not latest: notes.append('今日尚无累计快照')
    elif age is None or age > stale_minutes: notes.append('累计快照已过期或时间无效，不能代表当前结果')
    if invalid: notes.append('发现%d条无效时间或小时记录，需重新采集核对' % invalid)
    from . import collection_state
    if windows is None: windows = collection_state.load(root, account)
    missing_slots = [x for x in expected_slots if x not in slots]
    classification = collection_state.classify(missing_slots, windows)
    historic = len(classification['historical_missing_slots'])
    operational = len(classification['operational_missing_slots'])
    paused = len(classification['paused_missing_slots'])
    if historic: notes.append('启用前历史观察点缺少%d个，区间分析受限' % historic)
    if operational: notes.append('运行期间漏采%d个半小时观察点' % operational)
    if paused: notes.append('明确暂停期间缺少%d个观察点，不作为漏采故障' % paused)
    if classification['unknown_missing_slots']: notes.append('存在半小时缺口，采集启用起点尚未核对')
    components = {
        'hourly': 'invalid' if hour_invalid else 'incomplete' if missing else 'complete',
        'intervals': 'incomplete' if operational else 'limited' if missing_slots else 'complete',
        'snapshot': 'invalid' if invalid > hour_invalid else 'missing' if not latest else
                    'stale' if age is None or age > stale_minutes else 'fresh'}
    result = {'account': account, 'date': day, 'checked_at': now.isoformat(),
              'expected_hours': len(expected), 'covered_hours': len(covered), 'missing_hours': missing,
              'invalid_records': invalid, 'snapshot_cutoff': cutoff.isoformat() if cutoff else None,
              'snapshot_age_minutes': age, 'missing_snapshot_slots': missing_slots,
              **classification, 'components': components,
              'state': 'attention' if notes else 'checked', 'notes': notes,
              'scope': '经营：乘方商品报表；状态：商品视频全域（不含直播）；两者计划数不能互相替代。',
              'limit': '半小时为累计快照观测，非精确半小时成交归属。缺少早期快照影响区间比较，不等于最新累计金额缺失；空小时仅表示接口返回无记录。'}
    return result


def summary(value):
    components = value.get('components', {})
    labels = {'complete':'完整','incomplete':'有缺口','limited':'区间分析受限',
              'invalid':'校验失败','missing':'暂无','stale':'已过期','fresh':'新鲜','unknown':'待核对'}
    def label(key):return labels.get(components.get(key),'待核对')
    lines = ['小时报表：%s（已收集%d/%d个完整小时；缺失%s）。' %
             (label('hourly'),value['covered_hours'],value['expected_hours'],'、'.join(value['missing_hours']) or '无'),
             '半小时观察点：%s；启用前历史缺口%d个，运行期间漏采%d个，暂停期间缺口%d个。' %
             (label('intervals'),len(value.get('historical_missing_slots',[])),
              len(value.get('operational_missing_slots',[])),len(value.get('paused_missing_slots',[]))),
             '最新累计快照：%s；截至%s。' % (label('snapshot'),value['snapshot_cutoff'] or '暂无'),
             '完整累计快照与历史观察点是不同检查项；观察点缺口影响区间分析，不直接证明累计金额缺失。']
    if not value.get('coverage_registered',False):lines.append('采集启用起点尚未登记，历史与运行期分类待复核。')
    return '\n'.join(lines)

# health-assessment-v25
