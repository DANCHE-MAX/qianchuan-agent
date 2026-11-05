"""Validated yuan-denominated plan report for budget observation, no list stats."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

CHINA=timezone(timedelta(hours=8))
TOPIC='OVERALL_ROI_PRODUCT_PROJECT'
FIELDS={'ad_spend':'stat_cost_for_roi2','gmv':'total_pay_order_gmv_include_coupon_for_roi2',
        'backend_net':'total_order_settle_amount_for_roi2_1h','comprehensive_cost':'stat_cost_for_overall_roi2',
        'order_count':'total_pay_order_count_for_roi2'}


def instant(value):
    at=datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(at,datetime) or at.tzinfo is None:raise ValueError('报表时间须带时区')
    return at.astimezone(CHINA)


def fetch(env,at,request=None,clock=None):
    # Lazy import: hourly imports budget_diagnostics; avoid a module-load cycle.
    from . import monitor,analysis_gate,hourly
    requested=instant(at);cutoff=requested.replace(microsecond=0)
    snapshot=monitor.fetch_snapshot(env,'plan',now=cutoff,request=request)
    checked=instant((clock or (lambda:datetime.now(CHINA)))())
    account=hourly.account_id(env)
    parsed,error=analysis_gate.validate(snapshot,account,'plan',checked)
    if (error or checked.date()!=requested.date() or snapshot['request_end']!=cutoff.isoformat()
            or instant(snapshot['fetched_at'])>checked or instant(snapshot['fetched_at'])<cutoff
            or any(p.get('code')!=0 for p in snapshot['raw_pages'])):
        raise ValueError('预算经营报表账户、日期、来源或响应无效')
    rows={}
    for pid,row in parsed['rows'].items():
        clean={'id':pid,'name':row['name']}
        for key in FIELDS:
            value,problem=analysis_gate.number(row,key)
            if problem:raise ValueError('预算经营报表指标缺失或无效')
            clean[key]=str(value)
        rows[pid]=clean
    return {'state':'available' if rows else 'empty','advertiser_id':account,'day':requested.date().isoformat(),
            'topic':TOPIC,'unit':'CNY','requested_at':requested.isoformat(),
            'period_start':cutoff.replace(hour=0,minute=0,second=0).isoformat(),
            'cutoff':snapshot['request_end'],'fetched_at':snapshot['fetched_at'],
            'metric_fields':FIELDS,'rows':rows,'raw_pages':snapshot['raw_pages'],
            'scope':'乘方商品计划当日累计报表；不含直播及其他全域范围',
            'evidence_origin':'local_open_api_not_mcp','transport':'open_api'}


def record(performance,pid,day,read_at,account=None):
    from . import analysis_gate
    if not isinstance(performance,dict) or performance.get('state')=='unavailable':return None,'unavailable'
    if performance.get('state') not in ('available','empty'):return None,'invalid_report'
    try:
        read=instant(read_at);cutoff=instant(performance['cutoff']);fetched=instant(performance['fetched_at'])
        if ((account is not None and performance['advertiser_id']!=account)
                or performance['day']!=day or read.date().isoformat()!=day or cutoff.date().isoformat()!=day
                or performance['topic']!=TOPIC or performance['unit']!='CNY' or cutoff>read or fetched>read or fetched<cutoff):
            return None,'invalid_report'
        if read-cutoff>timedelta(minutes=45):return None,'stale_report'
        row=performance['rows'].get(pid)
        if row is None:return None,'no_record'
        if str(row['id'])!=pid:return None,'invalid_report'
        clean={'id':pid,'name':row['name']}
        for key in FIELDS:
            value,problem=analysis_gate.number(row,key)
            if problem:return None,'invalid_report'
            clean[key]=value
        return clean,'available'
    except (KeyError,ValueError,TypeError,AttributeError):return None,'invalid_report'


def ratios(row):
    if row is None:return {'payment_roi':None,'net_amount_div_ad_spend':None,'net_amount_div_comprehensive_cost':None}
    def divide(a,b):return str(row[a]/row[b]) if row[b] else None
    return {'payment_roi':divide('gmv','ad_spend'),'net_amount_div_ad_spend':divide('backend_net','ad_spend'),
            'net_amount_div_comprehensive_cost':divide('backend_net','comprehensive_cost')}


def unavailable(error):
    return {'state':'unavailable','rows':{},'error_type':type(error).__name__,
            'evidence_origin':'local_open_api_not_mcp','transport':'open_api',
            'note':'本次经营报表未获取，未以旧列表指标、历史日报或0代替。'}
