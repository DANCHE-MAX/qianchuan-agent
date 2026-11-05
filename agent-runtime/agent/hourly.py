"""Read-only hourly collection. No notifier, LLM, budget writes or daily model writes."""
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import qianchuan as q, qc_auth, budget_diagnostics
from .knowledge import tokens
from .reliability import file_lock
from . import diagnostics

CHINA = timezone(timedelta(hours=8))
CURRENT_WORDS = ('当前', '目前', '现在', '今日', '今天', '截至', '截止到', '实时')
FIELDS = dict(zip(('ad_spend', 'gmv', 'backend_net', 'comprehensive_cost'), q.METRICS.values()))
PLAN_SCOPE = '商品视频全域计划（不含直播）；不限制乘方场景及出价类型'


def now_china():
    return datetime.now(CHINA)


def is_current(command):
    # Explicit historical questions retain their existing date/report route.
    historical = re.search(r'昨天|昨日|前天|\d{1,2}[月./-]\d{1,2}|\d{4}年', command)
    return any(word in command for word in CURRENT_WORDS) and not historical


def account_id(env):
    value = env.get('QC_ADVERTISER_IDS', '').strip()
    if not value.isascii() or not value.isdigit():
        raise ValueError('小时采集需要一个明确的千川账户')
    return value


def floor_hour(now):
    if now.tzinfo is None:
        raise ValueError('小时采集时间必须带时区')
    return now.astimezone(CHINA).replace(minute=0, second=0, microsecond=0)


def fetch_hour(env, start, now=None, request=None):
    now = now or now_china()
    if start != floor_hour(start) or start >= floor_hour(now):
        raise ValueError('只能采集已结束的完整小时')
    end = start + timedelta(hours=1)
    params = {'advertiser_id': int(account_id(env)), 'data_topic': q.TOPIC,
              'dimensions': ['ad_id', 'ad_name', 'stat_time_day', 'range_stat_time_hour'],
              'metrics': list(FIELDS.values()), 'filters': [], 'order_by': [],
              'start_time': start.strftime('%Y-%m-%d %H:%M:%S'),
              'end_time': (end-timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S'),
              'page_size': 100}
    rows, pages, seen, expected = [], [], set(), None
    for page in range(1, 1001):
        params['page'] = page
        with diagnostics.api_label('qianchuan.hourly_report'):
            payload = qc_auth.call(env, lambda token: (request or q._get)(dict(params), token))
        if payload.get('code') != 0:
            raise ValueError('小时报表接口失败')
        try:
            data = payload['data']; info = data['page_info']; batch = data['rows']
            count, total_pages = int(info['total_number']), int(info['total_page'])
            if int(info['page']) != page or not isinstance(batch, list) or count < 0:
                raise ValueError()
            if count == 0:
                if page != 1 or batch or total_pages not in (0, 1): raise ValueError()
                pages.append(payload)
                break
            if expected is None: expected = (count, total_pages)
            if expected != (count, total_pages) or not batch or not 1 <= page <= total_pages:
                raise ValueError()
            for row in batch:
                dims = row['dimensions']
                pid, name = str(dims['ad_id']['Value']), str(dims['ad_name']['Value'])
                # The API returns a date/hour as well as its display-only hour range.
                hour = dims['stat_time_hour']['ValueStr']
                if (not pid.isascii() or not pid.isdigit() or not name.strip() or pid in seen
                        or dims['stat_time_day']['ValueStr'] != start.date().isoformat()
                        or hour not in (start.strftime('%Y-%m-%d %H:%M'), start.strftime('%Y-%m-%d %H:%M:%S'))):
                    raise ValueError()
                seen.add(pid)
                item = {'plan_id': pid, 'plan_name': name}
                for key, field in FIELDS.items():
                    item[key] = str(q.number(row['metrics'][field]['Value'], field))
                rows.append(item)
            pages.append(payload)
            if page == total_pages:
                if len(rows) != count: raise ValueError()
                break
        except (KeyError, TypeError, ValueError):
            raise ValueError('小时报表分页、时间维度、重复计划或金额校验失败') from None
    else:
        raise ValueError('小时报表超过分页上限')
    return {'advertiser_id': account_id(env), 'start': start.isoformat(), 'end': end.isoformat(),
            'fetched_at': now_china().isoformat(), 'monetary_unit': 'CNY',
            'rows': rows, 'pages': pages, 'metric_fields': FIELDS, 'data_topic': q.TOPIC}


def plan_list_window(day):
    """Same-day pair required by uni_promotion/list (40000 'start_time ... missing').

    Verified 2026-10-30 read-only: today's and yesterday's windows return the same
    50-plan ID set as the last no-time snapshot, including DISABLE and TIME_DONE
    plans, so the pair is a data-period argument rather than an inventory filter.
    """
    value = datetime.strptime(day, '%Y-%m-%d').date().isoformat()
    if value != day: raise ValueError('状态列表日期格式无效')
    return value + ' 00:00:00', value + ' 23:59:59'


def fetch_plan_list(env, day, request=None):
    """Account VIDEO_PROM_GOODS inventory, separate from Chuanfang diagnostics."""
    result, seen, expected = [], set(), None
    start_time, end_time = plan_list_window(day)
    for page in range(1, 1001):
        params = {'advertiser_id': account_id(env), 'marketing_goal': 'VIDEO_PROM_GOODS',
                  'filtering': {'status': 'ALL'}, 'page': page, 'page_size': 100,
                  'start_time': start_time, 'end_time': end_time}
        # adlab_scene=OVERALL_PROJECT restricts to Chuanfang; data_period stays omitted.
        with diagnostics.api_label('qianchuan.plan_list'):
            payload = qc_auth.call(env, lambda token: (request or budget_diagnostics._get)(params, token))
        try:
            data = payload['data']; info = data['page_info']; batch = data['ad_list']
            count, pages = int(info['total_num']), int(info['total_page'])
            if not isinstance(batch, list) or int(info['page']) != page or count < 0: raise ValueError()
            if count == 0:
                if page != 1 or batch or pages not in (0, 1): raise ValueError()
                break
            if expected is None: expected = (count, pages)
            if expected != (count, pages) or not batch or not 1 <= page <= pages: raise ValueError()
            for item in batch:
                ad = item['ad_info']; pid = str(ad['id'])
                if not pid.isascii() or not pid.isdigit() or pid in seen: raise ValueError()
                if not ad.get('status') or not ad.get('opt_status'): raise ValueError()
                seen.add(pid); result.append(item)
            if page == pages:
                if len(result) != count: raise ValueError()
                return result
        except (KeyError, TypeError, ValueError):
            raise ValueError('商品视频全域计划列表字段或分页不完整') from None
    else:
        raise ValueError('商品视频全域列表超过分页上限')
    return result


def problem_printable(exc, owner):
    name = type(exc).__name__
    changed = getattr(owner, 'last_list_problem', None) != name
    owner.last_list_problem = name
    return changed


class Store:
    def __init__(self, root):
        self.path = Path(root)/'data'/'qianchuan'/'hourly.db'

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS hours (account TEXT, start TEXT, payload TEXT, PRIMARY KEY(account,start))')
            db.execute('CREATE TABLE IF NOT EXISTS plan_lists (account TEXT PRIMARY KEY, payload TEXT)')
            with db: yield db
        finally:
            db.close()

    def save(self, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO hours VALUES (?,?,?)',
                       (value['advertiser_id'], value['start'], json.dumps(value, ensure_ascii=False)))

    def hours(self, account, day):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM hours WHERE account=? AND start>=? AND start<? ORDER BY start',
                (account, day+'T00:00:00+08:00',
                 (datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()+'T00:00:00+08:00'))]

    def save_plans(self, account, items, fetched_at):
        plans = [{'plan_id': str(x['ad_info']['id']), 'plan_name': x['ad_info'].get('name'),
                  'status': x['ad_info'].get('status'), 'opt_status': x['ad_info'].get('opt_status')}
                 for x in items]
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO plan_lists VALUES (?,?)',
                       (account, json.dumps({'fetched_at': fetched_at, 'scope': PLAN_SCOPE, 'plans': plans}, ensure_ascii=False)))

    def plans(self, account):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM plan_lists WHERE account=?', (account,)).fetchone()
        return json.loads(row[0]) if row else None


class Collector:
    def __init__(self, root, env):
        self.store, self.env = Store(root), env
        self.stop_event = threading.Event()
        self.thread = None
        self.last_problem = None

    def tick(self, now=None):
        now = now or now_china()
        boundary = floor_hour(now); account = account_id(self.env)
        # Startup catches today's gaps, and yesterday's last hour at midnight.
        begin = min(boundary-timedelta(hours=1), boundary.replace(hour=0))
        complete = {x['start'] for x in self.store.hours(account, begin.date().isoformat())}
        if begin.date() != boundary.date():
            complete.update(x['start'] for x in self.store.hours(account, boundary.date().isoformat()))
        with file_lock(self.store.path.with_suffix('.lock')):
            # Re-read under the process lock to avoid duplicate collection across restarts.
            complete.update(x['start'] for x in self.store.hours(account, begin.date().isoformat()))
            start = begin
            failures = []
            while start < boundary and not self.stop_event.is_set():
                if start.isoformat() not in complete:
                    try:
                        value = fetch_hour(self.env, start, now=now)
                        self.store.save(value)
                        print('小时采集已保存：账户%s %s—%s，%d条计划；不发送飞书。' %
                              (account, start.strftime('%m-%d %H:%M'), (start+timedelta(hours=1)).strftime('%H:%M'), len(value['rows'])), flush=True)
                    except Exception as exc:
                        # A gap must not prevent newer hours from being collected.
                        failures.append(type(exc).__name__)
                start += timedelta(hours=1)
            report_error = RuntimeError('部分小时采集失败，将重试') if failures else None
            # Plan status is a separately dated snapshot, never inferred from hourly activity.
            # Its failure is recorded on its own signal and must not mark complete reports failed.
            from . import health
            health.record(self.store.path.parents[2], self.env, '小时报表',
                          'failed' if failures else 'ok', failures[-1] if failures else '')
            previous = self.store.plans(account)
            if previous and previous.get('scope') == PLAN_SCOPE and datetime.fromisoformat(previous['fetched_at']) >= boundary:
                # Fresh this hour: keep the signal alive without inventing a new update time.
                signal = health.Store(self.store.path.parents[2]).signals(account).get('状态列表')
                if not signal or signal['state'] != 'failed':
                    health.record(self.store.path.parents[2], self.env, '状态列表', 'ok')
            else:
                try:
                    health.run(self.store.path.parents[2], self.env, '状态列表',
                               lambda: self.store.save_plans(account, fetch_plan_list(self.env, boundary.date().isoformat()), now_china().isoformat()))
                except Exception as exc:
                    if problem_printable(exc, self):
                        print('计划状态列表刷新暂未成功（%s）；小时报表不受影响，将重试。' % type(exc).__name__, flush=True)
            if report_error:
                raise report_error

    def work(self):
        from . import health
        while not self.stop_event.is_set():
            try:
                health.run(self.store.path.parents[2], self.env, '小时采集', self.tick)
                self.last_problem = None
            except Exception as exc:
                problem = type(exc).__name__
                if problem != self.last_problem:
                    print('小时采集暂未完成，将重试（%s）；不会发送飞书或用缺失小时冒充0。' % problem, flush=True)
                self.last_problem = problem
            self.stop_event.wait(60)

    def start(self):
        self.thread = threading.Thread(target=self.work, name='qianchuan-hourly', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)


def today_context(root, env, question, now=None):
    from . import monitor
    latest = monitor.today_context(root, env, question, now=now)
    if latest is not None:
        return latest
    now = now or now_china(); boundary = floor_hour(now); account = account_id(env)
    store = Store(root)
    hours = [x for x in store.hours(account, boundary.date().isoformat())
             if datetime.fromisoformat(x['end']) <= boundary]
    if not hours:
        return None
    expected = [boundary.replace(hour=h).isoformat() for h in range(boundary.hour)]
    covered = {x['start'] for x in hours}
    plans, hour_totals = {}, []
    for value in hours:
        totals = {key: Decimal(0) for key in FIELDS}
        for item in value['rows']:
            target = plans.setdefault(item['plan_id'], {'plan_id': item['plan_id'], 'plan_name': item['plan_name'],
                                                       **{key: Decimal(0) for key in FIELDS}})
            target['plan_name'] = item['plan_name']
            for key in FIELDS:
                amount = Decimal(item[key]); target[key] += amount; totals[key] += amount
        hour_totals.append({'start': value['start'], 'end': value['end'], **totals})
    totals = {key: sum((x[key] for x in hour_totals), Decimal(0)) for key in FIELDS}
    totals['payment_roi'] = totals['gmv']/totals['ad_spend'] if totals['ad_spend'] else None
    for plan in plans.values():
        plan['payment_roi'] = plan['gmv']/plan['ad_spend'] if plan['ad_spend'] else None
    query = tokens(question)
    ranked = sorted(plans.values(), key=lambda p: (-int(p['plan_id'] in question),
                    -len(query & tokens(p['plan_name'])), -p['ad_spend']))
    snapshot = store.plans(account)
    if snapshot and snapshot.get('scope') != PLAN_SCOPE: snapshot = None
    if snapshot and datetime.fromisoformat(snapshot['fetched_at']).date() != boundary.date(): snapshot = None
    current_plans = snapshot['plans'] if snapshot else []
    selected_current = sorted(current_plans, key=lambda p: (-int(p['plan_id'] in question),
                             -len(query & tokens(str(p['plan_name'])))))[:20]
    return json.dumps({'资料类型': '今日小时采集', '账户': account, '日期': boundary.date().isoformat(),
        '统计范围': {'经营数据': '乘方商品计划小时报表，仅此子范围的消耗与成交', '计划状态': PLAN_SCOPE},
        '本次提问时间': now.isoformat(), '覆盖小时': [{'start': x['start'], 'end': x['end']} for x in hours],
        '缺失小时': [s for s in expected if s not in covered], '最新采集时间': max(x['fetched_at'] for x in hours),
        'ROI口径': '整体支付ROI=整体成交金额/整体消耗；净成交和综合成本仅作辅助，不替代该主ROI。',
        '整体': totals, '逐小时': hour_totals, '有报表记录计划数': len(plans), '逐计划': ranked[:20],
        '状态列表读取时间': snapshot['fetched_at'] if snapshot else None,
        '状态列表计划总数': len(current_plans) if snapshot else None,
        '状态列表状态分布': dict(Counter(
            p['status']+' / '+p['opt_status'] for p in current_plans)) if snapshot else None,
        '状态列表在投数': sum(p['status']=='DELIVERY_OK' and p['opt_status']=='ENABLE' for p in current_plans) if snapshot else None,
        '状态列表部分计划': selected_current,
        '限制': '只汇总已成功采集的今日完整小时，不含正在进行的小时。覆盖不足或有缺口必须说明；空小时表示接口返回无记录，不保证无延迟或归因回补。经营报表只覆盖乘方，状态列表覆盖商品视频全域，两者范围不同，不能用报表记录数推断全域经营数据，不以两者差额推断无消耗。状态列表只代表自身读取时间，不代表此刻实时查询。在投必须同时满足ENABLE和DELIVERY_OK；不可投或已关闭不算在投。最多提供20条相关计划，可按名称ID查询其余。当前资料没有预算金额或利润预测，缺少时不得套用昨日日报或SOP示例金额。历史日报仍可单独查询。'}, ensure_ascii=False, default=str)

# health-v23

# budget-report-source-v32
