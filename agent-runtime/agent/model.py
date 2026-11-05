"""季节退款 / 利润 / GMV 消耗上限模型；纯计算，不写平台、不发消息。"""
import csv
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from .xlsx import read_rows
from .roi import payment_roi

D = Decimal
ZERO = D('0')
FIELDS = {'gmv': '整体成交金额', 'backend_net': '净成交金额',
          'ad_spend': '整体消耗', 'comprehensive_cost': '综合成本'}


def number(value, label):
    raw = str(value).strip().replace(',', '').replace('，', '')
    try:
        val = D(raw)
    except InvalidOperation:
        raise ValueError('%s 缺失或不是数字：%r' % (label, value))
    if not val.is_finite() or val < 0:
        raise ValueError('%s 必须是有限非负数' % label)
    return val


def read_daily(path):
    path = Path(path)
    if path.suffix.lower() == '.xlsx':
        raw = read_rows(str(path))
    elif path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as f:
            raw = list(csv.reader(f))
    else:
        raise ValueError('模型需要千川原始计划维度 .xlsx 或 .csv')
    if not raw:
        raise ValueError('数据文件为空')
    header = [str(x).strip() for x in raw[0]]
    required = set(FIELDS.values()) | {'计划ID', '计划名称', '日期', '小时区间'}
    missing = required - set(header)
    if missing:
        raise ValueError('需要计划维度原表，缺少字段：' + '、'.join(sorted(missing)))
    if len(header) != len(set(header)):
        raise ValueError('重复表头')
    return [dict(zip(header, [str(x).strip() for x in row]))
            for row in raw[1:] if any(str(x).strip() for x in row)]


def china_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def select_rows(rows, end, days, today=None):
    today = today or china_today()
    end = date.fromisoformat(end)
    if end >= today:
        raise ValueError('截止日必须早于上海当日，不能把当日数据当成完整日')
    if days <= 0:
        raise ValueError('窗口天数必须大于0')
    start = end - timedelta(days=days - 1)
    expected = {(start + timedelta(days=i)).isoformat() for i in range(days)}
    audit = {'raw_rows': len(rows), 'summary_rows': 0, 'hourly_rows': 0,
             'outside_window_rows': 0, 'selected_rows': 0}
    selected, seen, dates = [], set(), set()
    for row in rows:
        ds = row.get('日期', '')
        if ds in ('', '-', '全部'):
            audit['summary_rows'] += 1
            continue
        if row.get('小时区间') != '全部':
            audit['hourly_rows'] += 1
            continue
        date.fromisoformat(ds)  # 非法日期不能静默略过
        if ds not in expected:
            audit['outside_window_rows'] += 1
            continue
        pid = row.get('计划ID', '').strip()
        if not pid:
            raise ValueError('每日行缺少计划ID')
        key = (pid, ds)
        if key in seen:
            raise ValueError('重复计划每日行：%s / %s' % key)
        seen.add(key)
        dates.add(ds)
        item = {'plan_id': pid, 'plan_name': row['计划名称'], 'date': ds}
        item.update({k: number(row.get(col, ''), col) for k, col in FIELDS.items()})
        if item['backend_net'] > item['gmv']:
            raise ValueError('%s 后台净成交大于毛成交，请核实归因口径' % pid)
        if item['comprehensive_cost'] < item['ad_spend']:
            raise ValueError('%s 综合成本小于广告消耗' % pid)
        selected.append(item)
    missing = expected - dates
    if missing:
        raise ValueError('窗口缺少每日数据，不缩短分母：' + ', '.join(sorted(missing)))
    audit['selected_rows'] = len(selected)
    audit['covered_dates'] = sorted(dates)
    audit['start'] = start.isoformat()
    audit['end'] = end.isoformat()
    audit['days'] = days
    audit['completeness_note'] = '只验证文件内连续日期；导出范围、遗漏计划仍需业务确认'
    return selected, audit


def validate_config(cfg, season):
    if season not in cfg['refund_rates']:
        raise ValueError('未配置季节：' + season)
    ratio = number(cfg['purchase_multiplier'], '采购倍率')
    if ratio <= 0:
        raise ValueError('采购倍率必须大于0')
    for key in ('insurance_rate', 'platform_rate', 'commission_rate', 'gmv_cap_rate'):
        if number(cfg[key], key) > 1:
            raise ValueError(key + ' 必须在0至1之间')
    for val in cfg['refund_rates'][season].values():
        if number(val, '退款率') > 1:
            raise ValueError('退款率必须在0至1之间')
    margin = 1 - 1 / ratio - D(str(cfg['insurance_rate'])) - D(str(cfg['platform_rate']))
    if margin <= 0:
        raise ValueError('广告前利润率必须大于0')
    return margin


def evaluate(rows, cfg, season, end, today=None):
    margin = validate_config(cfg, season)
    days = int(cfg['window_days'])
    selected, audit = select_rows(rows, end, days, today)
    plans = {}
    for row in selected:
        pid = row['plan_id']
        p = plans.setdefault(pid, dict(plan_id=pid, plan_name=row['plan_name'],
                                       days_present=0, **{k: ZERO for k in FIELDS}))
        p['days_present'] += 1
        for key in FIELDS:
            p[key] += row[key]
    results = []
    for pid, p in plans.items():
        profile = cfg.get('plans', {}).get(pid, {})
        band = profile.get('refund_band')
        rate = cfg['refund_rates'][season].get(band)
        p['refund_band'] = band
        p['price_basis'] = profile.get('basis', '未配置价格档')
        p['refund_rate'] = D(str(rate)) if rate is not None else None
        p['payment_roi'] = payment_roi(p)
        p['comprehensive_roi'] = p['backend_net'] / p['comprehensive_cost'] if p['comprehensive_cost'] else None
        p['daily_spend'] = p['ad_spend'] / days
        p['daily_cap_reference'] = p['gmv'] / days * D(str(cfg['gmv_cap_rate']))
        p['spend_above_reference'] = p['daily_spend'] > p['daily_cap_reference']
        p['configured_budget'] = None  # 不能把实际消耗或参考上限冒充后台设置
        p['predicted_net'] = p['gmv'] * (1 - p['refund_rate']) if rate is not None else ZERO if not p['gmv'] else None
        for key in ('purchase_cost', 'insurance_cost', 'platform_cost', 'commission_cost',
                    'estimated_total_cost', 'predicted_profit', 'daily_breakeven_spend'):
            p[key] = None
        if p['predicted_net'] is not None:
            net = p['predicted_net']
            p['purchase_cost'] = net / D(str(cfg['purchase_multiplier']))
            p['insurance_cost'] = net * D(str(cfg['insurance_rate']))
            p['platform_cost'] = net * D(str(cfg['platform_rate']))
            p['commission_cost'] = ZERO  # Retained audit field; creator commission is excluded.
            # 后台综合成本仅作辅助指标，不用于利润扣减，达人佣金不计成本。
            p['estimated_total_cost'] = sum(p[k] for k in ('purchase_cost', 'insurance_cost', 'platform_cost')) + p['ad_spend']
            p['predicted_profit'] = net - p['estimated_total_cost']
            p['daily_breakeven_spend'] = net * margin / days
        p['status'] = ('需补价格档' if p['predicted_net'] is None else
                       '零成交：检查或测试，15%参考为0不等于自动停投' if not p['gmv'] else
                       '小样本：不据此放量' if p['ad_spend'] < D(str(cfg['small_sample_spend'])) else
                       '预计亏损：审查投放' if p['predicted_profit'] < 0 else
                       '超过15%消耗参考：核对后台设置' if p['spend_above_reference'] else
                       '在15%消耗参考内；不要求花满')
        results.append(p)
    results.sort(key=lambda p: p['ad_spend'], reverse=True)
    aggregate_keys = list(FIELDS) + ['predicted_net', 'purchase_cost', 'insurance_cost',
                                    'platform_cost', 'commission_cost', 'estimated_total_cost',
                                    'predicted_profit', 'daily_cap_reference', 'daily_breakeven_spend', 'daily_spend']
    totals = {k: sum(p[k] for p in results) if all(p[k] is not None for p in results) else None
              for k in aggregate_keys}
    totals['payment_roi'] = payment_roi(totals)
    totals['comprehensive_roi'] = totals['backend_net'] / totals['comprehensive_cost'] if totals['comprehensive_cost'] else None
    return {'model_version': cfg['version'], 'season': season, 'audit': audit,
            'assumptions': cfg, 'advertising_margin': margin,
            'totals': totals, 'plans': results,
            'notes': ['主ROI为整体支付ROI=整体成交金额/整体消耗；后台净成交/综合成本仅为辅助口径，不代表最终利润',
                      '退款按历史退款成功订单全额退款估算，托管使用季节整体率',
                      '模型成本只含采购、运费险、平台费和广告消耗；达人佣金不计入成本',
                      '15%为历史日均GMV的消耗参考，不是保证盈利或后台预算设置值',
                      '仅生成报告，不自动改预算；缺少价格档时不输出完整预计利润']}


def fmt(value):
    return '—' if value is None else format(value.quantize(D('0.01'), rounding=ROUND_HALF_UP), ',.2f')


def render(result):
    from .budgets import describe
    a, t = result['audit'], result['totals']
    season_label = {'autumn_winter': '秋冬', 'summer': '夏季'}.get(result['season'], result['season'])
    lines = ['# 千川投流模型报告',
             '%s 至 %s｜%d个完整日｜%s｜达人佣金不计成本' % (a['start'], a['end'], a['days'], season_label),
             '', '## 后台实测',
             '广告消耗 %s｜GMV %s｜后台净成交 %s｜综合成本 %s｜整体支付ROI %s' %
             tuple(fmt(t[k]) for k in ('ad_spend', 'gmv', 'backend_net', 'comprehensive_cost', 'payment_roi')),
             '', '## 模型预测',
             '采购倍率 %s；运费险 %s%%；平台 %s%%。模型成本包含采购、运费险、平台费和广告消耗；不计达人佣金。' %
             (result['assumptions']['purchase_multiplier'],
              fmt(D(str(result['assumptions']['insurance_rate']))*100),
              fmt(D(str(result['assumptions']['platform_rate']))*100)),
             '预测最终净成交 %s｜估计总成本 %s｜预计利润 %s' %
             tuple(fmt(t[k]) for k in ('predicted_net', 'estimated_total_cost', 'predicted_profit')),
             '日均实际消耗 %s｜15%%日消耗上限参考 %s｜固定成交下保本日消耗参考 %s' %
             tuple(fmt(t[k]) for k in ('daily_spend', 'daily_cap_reference', 'daily_breakeven_spend')),
             '', '## 逐计划']
    for p in result['plans']:
        rr = '未配置' if p['refund_rate'] is None else fmt(p['refund_rate'] * 100) + '%'
        lines.extend(['', '**%s**（%s）' % (p['plan_name'], p['plan_id']),
                      '实测：GMV %s｜广告费 %s｜后台净成交 %s｜综合成本 %s｜整体支付ROI %s' %
                      tuple(fmt(p[k]) for k in ('gmv', 'ad_spend', 'backend_net', 'comprehensive_cost', 'payment_roi')),
                      '预测：退款率 %s｜最终净成交 %s｜总成本 %s｜利润 %s' %
                      (rr, fmt(p['predicted_net']), fmt(p['estimated_total_cost']), fmt(p['predicted_profit'])),
                      '日均消耗 %s｜15%%参考 %s｜保本消耗参考 %s' %
                      tuple(fmt(p[k]) for k in ('daily_spend', 'daily_cap_reference', 'daily_breakeven_spend')),
                      '依据：%s；%s。' % (p['price_basis'], p['status']), describe(p)])
    lines += ['', '## 数据体检',
              '原始%d行；排除跨日汇总%d行、小时明细%d行、窗口外%d行；采用%d行。' %
              tuple(a[k] for k in ('raw_rows', 'summary_rows', 'hourly_rows', 'outside_window_rows', 'selected_rows')),
              a['completeness_note'], '', '## 口径说明']
    lines += ['- ' + s for s in result['notes']]
    return '\n'.join(lines)


def save(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = 'model_%s_%s_%s' % (result['audit']['start'], result['audit']['end'], result['season'])
    account = str((result.get('source_api') or {}).get('advertiser_id') or '')
    if account:
        if not account.isascii() or not account.isdigit():
            raise ValueError('报告来源账户ID无效')
        stem = 'model_' + account + '_' + stem.removeprefix('model_')
    md = directory / (stem + '.md')
    md.write_text(render(result), encoding='utf-8')
    def encode(value):
        if isinstance(value, D):
            return str(value)  # 精度保留，消费者显式解析
        raise TypeError(type(value).__name__)
    (directory / (stem + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=encode), encoding='utf-8')
    return md


def message_chunks(text, limit=3500):
    """飞书按段分页，避免 notifier 截断尾部计划或口径说明。"""
    parts, current = [], ''
    for line in text.splitlines(keepends=True):
        if len(line.encode('utf-8')) > limit:
            raise ValueError('报告单行过长，请缩短商品名称')
        if len((current + line).encode('utf-8')) > limit:
            parts.append(current)
            current = ''
        current += line
    if current:
        parts.append(current)
    return parts
