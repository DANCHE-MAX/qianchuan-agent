"""Evidence-only readiness projection. Never supplies formal B or refund authority."""
from decimal import Decimal,InvalidOperation
import re

def amount(value):
    if value is None or isinstance(value,bool):return None
    try:
        d=Decimal(str(value))
        return str(d) if d.is_finite() and d>=0 else None
    except InvalidOperation:return None

def budget_fact(setting):
    return {'current':amount(setting.get('budget')),'mode':setting.get('budget_mode'),
            'formal_B':None,'formal_B_status':'await_authorized_go_live_snapshot',
            'execution_allowed':False}

def budget_events(rows,pid):
    result=[];seen=set()
    for row in rows:
        if str(row.get('object_id'))!=str(pid):continue
        for content in row.get('content_log',[]):
            change=re.fullmatch(r'修改 预算: 日预算([0-9]+(?:\.[0-9]+)?)元 -> 日预算([0-9]+(?:\.[0-9]+)?)元',content)
            creation=re.fullmatch(r'预算: 日预算([0-9]+(?:\.[0-9]+)?)元',content) if row.get('content_title')=='新建' else None
            if not(change or creation):continue
            before,after=change.groups() if change else (None,creation.group(1))
            record={'at':row.get('create_time'),'log_id':row.get('log_id'),'before':before,'after':after,'kind':'change' if change else 'creation','actor_classification':'unverified'}
            key=tuple(record.values())
            if key not in seen:result.append(record);seen.add(key)
    return sorted(result,key=lambda r:r['at'] or '')

def product_fact(response,pid):
    base={'product_id':str(pid),'refund_verified':False,'price_unit_verified':False,
          'historical_price_verified':False,'note':'当前价格原值；未确认官方单位、历史价格及季节/映射适用性，不进入利润判定'}
    if response.get('code')!=0:return dict(base,status='api_failed',code=response.get('code'))
    rows=[r for r in response.get('data',{}).get('product_list',[]) if str(r.get('id'))==str(pid)]
    if len(rows)!=1:return dict(base,status='missing' if not rows else 'ambiguous')
    r=rows[0]
    return dict(base,status='observed',raw_prices={k:amount(r.get(k)) for k in ('discount_lower_price','discount_higher_price','discount_price','market_price')})

def conditional_daily(rows,as_of,refund):
    from .daily_rules import assess_daily
    result=assess_daily(rows,as_of,dict(refund,verified=True))
    result['refund_verified']=False
    result['refund_assumed_for_simulation']=True
    result['simulation']=True
    return result

def pagination_audit(pages):
    infos=[p.get('data',{}).get('page_info',{}) for p in pages]
    totals=[i.get('total_number') for i in infos]
    total_pages=[i.get('total_page') for i in infos]
    rows=[r for p in pages for r in p.get('data',{}).get('logs',[])]
    ids=[r.get('log_id') for r in rows]
    consistent=bool(infos) and all(p.get('code')==0 for p in pages) and len(set(totals))==1 and len(set(total_pages))==1 and [i.get('page') for i in infos]==list(range(1,len(infos)+1)) and total_pages[-1]==len(pages)
    # Repeated log_id may denote split contents; do not infer full event coverage.
    complete=consistent and totals[0]==len(rows) and None not in ids and len(set(ids))==len(ids)
    return {'pagination_consistent':consistent,'completeness_verified':complete,'total_counts':totals,'returned_rows':len(rows),'unique_log_ids':len(set(ids)),
            'note':'重复日志ID可能是内容分组，不据此删合；数量或分页不一致时保守待核'}

def load_evidence(path,manifest):
    import hashlib,json
    raw=path.read_bytes()
    manifest[str(path)]=hashlib.sha256(raw).hexdigest()
    return json.loads(raw)
