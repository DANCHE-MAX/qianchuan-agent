"""Four-hour observed balance decline and fixed-speed runway, read-only."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from . import hourly as h
from .card_view import card, text_element

HOURS = tuple(range(0, 24, 2))


def stamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None: raise ValueError('余额样本缺少时区')
    return parsed.astimezone(h.CHINA)


def rate(samples, current, hours=4, tolerance_minutes=5, *, window_minutes=None, max_gap_minutes=35):
    """Use actual elapsed time; require continuous half-hour evidence, even at 00:00."""
    window_hours = window_minutes/60 if window_minutes is not None else hours
    label = ('%d分钟' % window_minutes) if window_minutes is not None else ('%d小时' % hours)
    unavailable = lambda reason: {'rate':None, 'reason':reason, 'window_hours':window_hours}
    if type(hours) is not int or not 1 <= hours <= 24: raise ValueError('余额速率窗口无效')
    if window_minutes is not None and (type(window_minutes) is not int or not 1 <= window_minutes <= 1440): raise ValueError('余额分钟窗口无效')
    if not 0 < max_gap_minutes <= 35: raise ValueError('余额采样间隔无效')
    duration = timedelta(minutes=window_minutes) if window_minutes is not None else timedelta(hours=hours)
    try:
        end = stamp(current['fetched_at']); target = end-duration
        account = str(current['advertiser_id']); unique = {}
        for sample in [*samples, current]:
            if str(sample['advertiser_id']) != account: continue
            at = stamp(sample['fetched_at'])
            if at < target-timedelta(minutes=tolerance_minutes) or at > end: continue
            amounts = {key:Decimal(str(sample['amounts_yuan'][key]))
                       for key in ('account_total','account_valid','account_frozen')}
            if any(not number.is_finite() for number in amounts.values()): raise ValueError()
            if at in unique and unique[at][1] != amounts:
                return unavailable('同一时间余额样本冲突，无法计算消耗速率')
            unique[at] = (sample, amounts)
        candidates = [at for at in unique if abs((at-target).total_seconds()) <= tolerance_minutes*60]
        if not candidates: return unavailable('缺少%s前的余额基准，暂无法计算近%s速率' % (label,label))
        start = min(candidates,key=lambda at:abs((at-target).total_seconds()))
        window = sorted(at for at in unique if start <= at <= end)
        if any((b-a).total_seconds()>max_gap_minutes*60 for a,b in zip(window,window[1:])):
            return unavailable('近%s余额采样有缺口，无法排除充值或资金调整影响' % label)
        for a,b in zip(window,window[1:]):
            av,bv = unique[a][1],unique[b][1]
            if bv['account_total'] > av['account_total'] or bv['account_frozen'] != av['account_frozen']:
                return unavailable('窗口内疑似充值、入账或冻结调整，余额净变动不能当作消耗速率')
            if abs((av['account_valid']-bv['account_valid'])-(av['account_total']-bv['account_total'])) > Decimal('.01'):
                return unavailable('可用余额与总余额变化不一致，需要核对资金流水')
        elapsed = Decimal(str((end-start).total_seconds()))/3600
        if elapsed <= 0: raise ValueError()
        decline = unique[start][1]['account_total']-unique[end][1]['account_total']
        return {'rate':str(decline/elapsed), 'start':start.isoformat(), 'end':end.isoformat(),
                'decline':str(decline), 'elapsed_hours':str(elapsed), 'window_hours':window_hours,
                'reason':'余额净下降观测速率；无法识别采样间被消耗抵消的充值，不等于广告消耗流水核算'}
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError):
        return unavailable('余额样本时间或金额无效，暂不提供速率与续航预测')


def notice(snapshot, value):
    label = '两小时' if value.get('window_hours') == 2 else '四小时'
    now = stamp(snapshot['fetched_at']); available = Decimal(str(snapshot['amounts_yuan']['account_valid']))
    if not available.is_finite(): raise ValueError('余额金额无效')
    lines = ['账户%s｜余额读取于%s（北京时间）' % (snapshot['advertiser_id'],now.strftime('%m-%d %H:%M:%S')),
             '当前可用余额：%.2f元' % available]
    speed = Decimal(str(value['rate'])) if value['rate'] is not None else None
    if speed is None:
        lines += ['最近四小时平均余额消耗速率：无法计算。'+value['reason'],
                  '预计可用时长：0小时，当前已无可用余额。' if available <= 0 else
                  '预计可用时长：无法估算，请等待完整采样或核对充值与资金流水。']
    else:
        if not speed.is_finite() or speed < 0: raise ValueError('余额速率无效')
        lines += ['观察窗口：%s—%s。' % (stamp(value['start']).strftime('%m-%d %H:%M'),stamp(value['end']).strftime('%m-%d %H:%M')),
                  '最近四小时平均余额净下降速率：%s。' %
                  ('低于0.01元/小时' if 0 < speed < Decimal('.01') else '%.2f元/小时' % speed)]
        if available <= 0:
            lines.append('预计可用时长：0小时，当前已无可用余额。')
        elif speed == 0:
            lines.append('预计可用时长：暂无法估算，近四小时未观察到余额净下降；不代表余额可无限使用。')
        else:
            hours = available/speed
            lines.append('按目前速率，余额预计还能用约%.2f小时（按余额降至0元估算）。' % hours)
            if hours <= 24*365:
                deadline = now+timedelta(seconds=float(hours*3600))
                lines.append('预计用尽时间：%s（北京时间）。' % deadline.strftime('%m-%d %H:%M'))
            reserve = max(Decimal(0),available-500)/speed
            lines.append('降至500元参考线约剩%.2f小时。' % reserve)
    if available <= 500: lines.append('充值预警：当前可用余额已不高于500元，请及时充值。')
    lines.append('本轮未计算余额速率和续航；低余额阈值预警单独判断。' if speed is None else
                 '按近四小时余额净下降速率保持不变估算；充值、资金调整及后续消耗变化会影响结果，不是精确耗尽承诺。')
    rendered = card('千川余额｜'+('充值预警' if available<=500 else '四小时检查'),[text_element('\n'.join(lines))])
    if label == '两小时':
        import json
        rendered = json.loads(json.dumps(rendered,ensure_ascii=False).replace('四小时','两小时'))
    rendered['header']['template'] = 'red' if available<=500 else 'blue'
    return rendered
