"""Evidence admission for closed-day reports and first-enabled operation logs."""
from datetime import datetime,date,timedelta
from decimal import Decimal,InvalidOperation
from collections import defaultdict
from zoneinfo import ZoneInfo
import re
CHINA=ZoneInfo('Asia/Shanghai')
FIELDS={'spend':'stat_cost_for_roi2','gmv':'total_pay_order_gmv_include_coupon_for_roi2','orders':'total_pay_order_count_for_roi2'}

def at(value):
    d=datetime.fromisoformat(value)
    return d.replace(tzinfo=CHINA) if d.tzinfo is None else d.astimezone(CHINA)

def complete_pages(bundle,key):
    if bundle.get('pages_complete') is not True:raise ValueError('incomplete_pages')
    result=[];expected=None;pages=bundle['pages']
    if not pages:raise ValueError('missing_pages')
    for n,p in enumerate(pages,1):
        if p.get('code')!=0:raise ValueError('api_failure')
        data=p['data'];info=data['page_info'];pair=(info['total_number'],info['total_page'])
        if expected is None:expected=pair
        if pair!=expected or info['page']!=n or not isinstance(data[key],list):raise ValueError('pagination_changed')
        result.extend(data[key])
    if expected[0]<0 or expected[1]<0 or len(pages)!=max(1,expected[1]) or len(result)!=expected[0]:raise ValueError('pagination_count_mismatch')
    return result

def first_enabled(bundle):
    unknown={'verified':False,'first_enabled_at':None,'reason':'first_enabled_evidence_unproven'}
    try:
        logs=complete_pages(bundle,'logs');pid=str(bundle['plan_id']);created=at(bundle['create_time'])
        if at(bundle['query_start']).date()>created.date() or at(bundle['query_end'])<created:return unknown
        entries=[]
        for parent in logs:
            entries.append(parent);entries.extend(parent.get('sub_logs') or [])
        initial=[];enables=[]
        for row in entries:
            if str(row.get('object_id'))!=pid:continue
            timestamp=at(row['create_time']);title=row.get('content_title');content=row.get('content_log') or []
            for line in content:
                line=line.replace('：',':').strip()
                if title=='新建' and timestamp.date()==created.date():
                    m=re.fullmatch(r'是否开启:\s*(开启|关闭)',line)
                    if m:initial.append((timestamp,m[1],row.get('log_id')))
                if title=='修改' and re.fullmatch(r'修改 (?:乘方状态|全域投放状态|投放状态):\s*(?:系统暂停|暂停|关闭)\s*->\s*开启',line):
                    enables.append((timestamp,row.get('log_id')))
        if not initial:return unknown
        earliest=min(x[0] for x in initial);states={x[1] for x in initial if x[0]==earliest}
        if len(states)!=1:return dict(unknown,reason='conflicting_creation_state')
        if '开启' in states:
            chosen=min((x[0],x[2]) for x in initial if x[1]=='开启')
            method='explicit_enabled_at_creation'
        else:
            candidates=[x for x in enables if x[0]>=earliest]
            if not candidates:return unknown
            chosen=min(candidates);method='explicit_disabled_creation_then_first_enable'
        if not chosen[1]:return unknown
        return {'verified':True,'first_enabled_at':chosen[0].isoformat(),'reason':method,'log_id':chosen[1],
                'created_at':created.isoformat(),'later_enable_count':sum(t>chosen[0] for t,_ in enables)}
    except (KeyError,TypeError,ValueError):return unknown

def amount(value):
    if value is None or isinstance(value,bool):raise ValueError('metric_missing')
    try:d=Decimal(str(value))
    except InvalidOperation:raise ValueError('metric_invalid') from None
    if not d.is_finite() or d<0:raise ValueError('metric_invalid')
    return d

def parse_daily(bundle):
    try:
        start=date.fromisoformat(bundle['requested_start']);end=date.fromisoformat(bundle['requested_end'])
        if start>end or end>=at(bundle['fetched_at']).date():raise ValueError('not_closed_calendar_days')
        if bundle['params']['data_topic']!='OVERALL_ROI_PRODUCT_PROJECT':raise ValueError('report_scope_mismatch')
        if bundle['granularity'] not in ('plan','day'):raise ValueError('granularity_invalid')
        result=[];seen=set()
        for raw in complete_pages(bundle,'rows'):
            day=raw['dimensions']['stat_time_day']['ValueStr'];d=date.fromisoformat(day)
            pid=str(raw['dimensions']['ad_id']['Value']) if bundle['granularity']=='plan' else None
            if not start<=d<=end or (pid is not None and (not pid.isascii() or not pid.isdigit())):raise ValueError('dimension_invalid')
            key=(pid,day)
            if key in seen:raise ValueError('duplicate_plan_day')
            seen.add(key);metrics={k:amount(raw['metrics'][field]['Value']) for k,field in FIELDS.items()}
            if metrics['orders']!=metrics['orders'].to_integral_value():raise ValueError('orders_not_integer')
            result.append({'account_id':str(bundle['account_id']),'plan_id':pid,'day':day,'spend':str(metrics['spend']),
                'gmv':str(metrics['gmv']),'orders':int(metrics['orders']),'reported_at':bundle['fetched_at'],
                'calendar_day_closed':True,'complete':False,'final_settlement':False})
        return result
    except (KeyError,TypeError,InvalidOperation) as e:raise ValueError('daily_contract_invalid') from None

def reconcile(plans,totals):
    summed=defaultdict(lambda:{k:Decimal(0) for k in FIELDS})
    for row in plans:
        for key in FIELDS:summed[row['day']][key]+=Decimal(str(row[key]))
    target={row['day']:{key:Decimal(str(row[key])) for key in FIELDS} for row in totals}
    return {day:day in summed and day in target and summed[day]==target[day] for day in sorted(set(summed)|set(target))}
