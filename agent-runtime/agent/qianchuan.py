"""Read-only Qianchuan project/day report; preserve raw API evidence and exact units."""
import csv
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .model import china_today, number, select_rows
from . import qc_auth

ENDPOINT = 'https://api.oceanengine.com/open_api/v1.0/qianchuan/report/uni_promotion/data/get/'
TOPIC = 'OVERALL_ROI_PRODUCT_PROJECT'
# Official report config: monetary Value fields are yuan; no scaling or ROI averaging.
METRICS = {'整体消耗': 'stat_cost_for_roi2',
           '整体成交金额': 'total_pay_order_gmv_include_coupon_for_roi2',
           '净成交金额': 'total_order_settle_amount_for_roi2_1h',
           '综合成本': 'stat_cost_for_overall_roi2'}
HEADERS = ['计划ID', '计划名称', '日期', '小时区间', *METRICS]


def _get(params, token):
    query = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                   for k, v in params.items()})
    request = urllib.request.Request(ENDPOINT + '?' + query, headers={'Access-Token': token})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def convert(rows, start, end):
    converted = []
    for row in rows:
        try:
            dims, metrics = row['dimensions'], row['metrics']
            pid = str(dims['ad_id']['Value'])
            name = str(dims['ad_name']['Value'])
            day = dims['stat_time_day']['ValueStr']
            date.fromisoformat(day)
            if not pid.isdigit() or not name.strip() or not start <= day <= end:
                raise ValueError('unexpected dimension')
            item = {'计划ID': pid, '计划名称': name, '日期': day, '小时区间': '全部'}
            for column, field in METRICS.items():
                item[column] = str(number(metrics[field]['Value'], column))
            converted.append(item)
        except (KeyError, TypeError, ValueError):
            raise ValueError('千川报表字段缺失、日期越界或金额无效；不以0替代缺失数据') from None
    return converted


def fetch_daily(env, end, days=1, today=None, request=None):
    today = today or china_today()
    end_date = date.fromisoformat(end)
    if end_date >= today or not 1 <= days <= 90:
        raise ValueError('千川日报必须取上海当日之前的完整日，窗口为1至90天')
    ids = [x.strip() for x in env.get('QC_ADVERTISER_IDS', '').split(',') if x.strip()]
    if len(ids) != 1 or not re.fullmatch(r'\d+', ids[0]):
        raise ValueError('当前模型只支持一个明确广告账户，请核对 QC_ADVERTISER_IDS')
    token = env.get('QC_ACCESS_TOKEN')
    if not token:
        raise ValueError('缺少 QC_ACCESS_TOKEN，请在本机 config/.env 配置')
    start = (end_date - timedelta(days=days-1)).isoformat()
    params = {'advertiser_id': int(ids[0]), 'data_topic': TOPIC,
              'dimensions': ['ad_id', 'ad_name', 'stat_time_day'],
              'metrics': list(METRICS.values()), 'filters': [], 'order_by': [],
              'start_time': start + ' 00:00:00', 'end_time': end + ' 23:59:59',
              'page': 1, 'page_size': 100}
    raw_pages, rows, expected = [], [], None
    for page in range(1, 1001):
        params['page'] = page
        payload = qc_auth.call(env, lambda current: (request or _get)(dict(params), current))
        if payload.get('code') != 0:
            raise ValueError('千川报表接口失败，code=%s；未生成或覆盖日报' % payload.get('code'))
        try:
            data = payload['data']
            info = data['page_info']
            count, pages = int(info['total_number']), int(info['total_page'])
            batch = data['rows']
            if not isinstance(batch, list) or int(info['page']) != page:
                raise ValueError()
            if count <= 0 or pages <= 0:
                raise ValueError()
            if expected is None:
                expected = (count, pages)
            if expected != (count, pages) or not batch or page > pages:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError('千川报表为空或分页不完整；请核对账户、日期与报表权限，不生成零值日报') from None
        rows.extend(convert(batch, start, end))
        raw_pages.append(payload)
        if page == pages:
            if len(rows) != count:
                raise ValueError('千川报表条数与分页总数不符，未生成日报')
            break
    else:
        raise ValueError('千川报表超过分页上限，未生成日报')
    # Validate uniqueness, full date coverage and field relationships before persistence.
    select_rows(rows, end, days, today=today)
    metadata = {'advertiser_id': ids[0], 'data_topic': TOPIC, 'start': start, 'end': end,
                'days': days, 'fetched_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                'monetary_unit': 'CNY', 'metric_fields': METRICS, 'pages': raw_pages}
    return rows, metadata


def save_snapshot(rows, metadata, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('qianchuan_%s_%s_%s.csv' % (
        metadata['advertiser_id'], metadata['start'], metadata['end']))
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    metadata = dict(metadata, csv_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    path.with_suffix('.api.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def provenance(path):
    sidecar = Path(path).with_suffix('.api.json')
    if not sidecar.exists():
        return None
    metadata = json.loads(sidecar.read_text(encoding='utf-8'))
    if metadata.get('csv_sha256') != hashlib.sha256(Path(path).read_bytes()).hexdigest():
        raise ValueError('API快照CSV已改动，与原始返回不一致，请重新拉取')
    return {k: v for k, v in metadata.items() if k != 'pages'}
