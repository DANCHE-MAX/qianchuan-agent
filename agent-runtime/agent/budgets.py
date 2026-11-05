"""Read-only current budget snapshots, separate from historical performance."""
import json
import re
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from . import qc_auth
from .reliability import ServiceError

ENDPOINT = 'https://api.oceanengine.com/open_api/v1.0/qianchuan/uni_promotion/ad/detail/'
MODES = {'BUDGET_MODE_DAY': '日预算', 'BUDGET_MODE_TOTAL': '总预算',
         'BUDGET_MODE_INFINITE': '不限预算'}


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='microseconds')


def _get(account, pid, token):
    url = ENDPOINT + '?' + urllib.parse.urlencode({'advertiser_id': account, 'ad_id': pid})
    req = urllib.request.Request(url, headers={'Access-Token': token})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def parse(payload, pid, fetched_at):
    base = {'plan_id': pid, 'fetched_at': fetched_at, 'amount': None,
            'mode': None, 'status': 'unavailable'}
    if not isinstance(payload, dict) or payload.get('code') != 0:
        return dict(base, reason='预算接口返回错误')
    data = payload.get('data') or {}
    if str(data.get('ad_id')) != pid:
        return dict(base, reason='预算接口计划ID不匹配')
    setting = data.get('delivery_setting') or {}
    mode = setting.get('budget_mode')
    if mode not in MODES:
        return dict(base, reason='预算类型缺失或不支持')
    base['mode'] = mode
    if mode == 'BUDGET_MODE_INFINITE':
        return dict(base, status='ok')  # 0/sentinel must never be rendered as a finite cap.
    try:
        amount = Decimal(str(setting['budget']))
        if not amount.is_finite() or amount < 0:
            raise ValueError()
    except (KeyError, TypeError, InvalidOperation, ValueError):
        return dict(base, reason='预算金额缺失或无效')
    return dict(base, status='ok', amount=str(amount))


def fetch(env, account, plans, request=None):
    account = str(account or '')
    if not account.isdigit() or env.get('QC_ADVERTISER_IDS', '').strip() != account:
        raise ValueError('报告账户与当前API配置不一致或未标明账户，不能混用预算')
    token = env.get('QC_ACCESS_TOKEN', '').strip()
    if not token:
        raise ValueError('未配置 QC_ACCESS_TOKEN，无法读取预算')
    ids = list(dict.fromkeys(str(p['plan_id']) for p in plans))
    if not ids or any(not pid.isdigit() for pid in ids):
        raise ValueError('没有有效计划ID可用于查询预算')
    def one(pid):
        try:
            payload = qc_auth.call(env, lambda current: (request or _get)(account, pid, current))
            return parse(payload, pid, now())
        except ServiceError as exc:
            return {'plan_id': pid, 'fetched_at': now(), 'status': 'unavailable',
                    'amount': None, 'mode': None, 'reason': str(exc)}
        except Exception:
            # Do not log server responses, request headers or exception strings.
            return {'plan_id': pid, 'fetched_at': now(), 'status': 'unavailable',
                    'amount': None, 'mode': None, 'reason': '预算读取失败或超时'}
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(one, ids))
    return {'advertiser_id': account, 'fetched_at': now(), 'kind': 'current_budget',
            'plans': rows, 'scope_note': '仅查询本次所选报告中的计划；当前设置不代表历史日报日期的预算'}


def save(snapshot, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Append-only snapshots preserve the chronology, including failed attempts.
    stamp = re.sub(r'[^0-9]', '', snapshot['fetched_at'])
    path = directory / ('budgets_%s_%s.json' % (snapshot['advertiser_id'], stamp))
    with path.open('x', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return path


def latest(directory, account):
    if not str(account or '').isdigit():
        return None
    candidates = []
    for p in Path(directory).glob('budgets_%s_*.json' % account):
        try:
            s = json.loads(p.read_text(encoding='utf-8'))
            if s.get('advertiser_id') == str(account) and s.get('kind') == 'current_budget':
                candidates.append((s['fetched_at'], s))
        except (OSError, ValueError, KeyError):
            continue
    if not candidates:
        return None
    merged = {}
    ordered = sorted(candidates, key=lambda pair: pair[0])
    for _, snapshot in ordered:
        for p in snapshot['plans']:
            previous = merged.get(p['plan_id'])
            if previous is None or p['fetched_at'] >= previous['fetched_at']:
                merged[p['plan_id']] = p
    return dict(ordered[-1][1], plans=list(merged.values()))


def attach(report, snapshot):
    account = (report.get('source_api') or {}).get('advertiser_id')
    if not snapshot or str(account) != snapshot.get('advertiser_id'):
        return
    by_id = {p['plan_id']: p for p in snapshot['plans']}
    for p in report['plans']:
        b = by_id.get(str(p['plan_id']))
        if b:
            p['budget_snapshot'] = dict(b)
            p['configured_budget'] = b['amount'] if b['status'] == 'ok' else None
    report['budget_scope_note'] = snapshot['scope_note']


def describe(plan):
    b = plan.get('budget_snapshot')
    if not b:
        return '后台预算：未采集'
    stamp = b.get('fetched_at', '未知时间')
    try:
        stamp = datetime.fromisoformat(stamp).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）'
    except (TypeError, ValueError):
        stamp = '未知时间' 
    if b.get('status') != 'ok':
        return '后台预算：未获取（%s；尝试时间%s）' % (b.get('reason', '读取失败'), stamp)
    label = MODES.get(b.get('mode'), '未知类型')
    if b.get('mode') != 'BUDGET_MODE_INFINITE':
        label += ' %s 元' % format(Decimal(b['amount']), ',.2f')
    return '后台预算快照：%s；读取时间%s；非历史预算' % (label, stamp)
