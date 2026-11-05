"""Closed-day net/settlement GET evidence, isolated from budget eligibility."""
import json,hashlib,os
from pathlib import Path
from datetime import datetime,date,timedelta
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo
from .read_transport import ReadClient
from .daily_refresh import Paced,atomic,write
from .history import complete_pages
from .collection_scope import VERSION,floor_day
CHINA=ZoneInfo('Asia/Shanghai')
FIELDS={'spend':'stat_cost_for_roi2','gmv':'total_pay_order_gmv_include_coupon_for_roi2','orders':'total_pay_order_count_for_roi2',
 'net_1h':'total_order_settle_amount_for_roi2_1h','net_orders_1h':'total_order_settle_count_for_roi2_1h',
 'refund_orders_1h':'total_refund_order_count_for_roi2_1h',
 **{'settled_'+str(n):'total_order_settle_amount_for_roi2_'+str(n)+'d' for n in (7,14,30,90)},
 **{'settled_orders_'+str(n):'total_order_settle_count_for_roi2_'+str(n)+'d' for n in (7,14,30,90)}}
def normalize(rows,kind,start,end):
 result=[];seen=set()
 for raw in rows:
  day=raw['dimensions']['stat_time_day']['ValueStr'];date.fromisoformat(day)
  pid=str(raw['dimensions']['ad_id']['Value']) if kind=='plan' else None
  if not start<=day<=end or (pid is not None and (not pid.isascii() or not pid.isdigit())):raise ValueError('dimension_mismatch')
  key=(pid,day)
  if key in seen:raise ValueError('duplicate_plan_day')
  seen.add(key);r={'plan_id':pid,'day':day}
  for k,f in FIELDS.items():
   value=raw.get('metrics',{}).get(f,{}).get('Value');number=None
   try:
    n=Decimal(str(value))
    if not isinstance(value,bool) and n.is_finite() and n>=0 and ('orders' not in k or n==n.to_integral_value()):number=str(n)
   except (InvalidOperation,ValueError):pass
   r[k]=number
  result.append(r)
 return result

def compare(plans,totals):
 result={}
 for day in sorted({r['day'] for r in plans+totals}):
  ps=[r for r in plans if r['day']==day];ts=[r for r in totals if r['day']==day]
  result[day]=bool(ps and len(ts)==1 and all(all(r[k] is not None for r in ps+ts) and sum((Decimal(r[k]) for r in ps),Decimal(0))==Decimal(ts[0][k]) for k in FIELDS))
 return result

def settlement_view(row,as_of_day):
 age=(date.fromisoformat(as_of_day)-date.fromisoformat(row['day'])).days
 return {'day':row['day'],'payment_gmv':row['gmv'],'paid_orders':row['orders'],'net_gmv_1h':row['net_1h'],'net_orders_1h':row['net_orders_1h'],
  'settlement_windows':{str(n):{'amount':row['settled_'+str(n)],'orders':row['settled_orders_'+str(n)],'elapsed':age>n} for n in (7,14,30,90)},
  'actual_profit_verified':False,'note':'1小时净成交及滚动结算不是最终利润；窗口未走完仍可修订，未回写预算利润模型'}

def fetch(c,start,end,kind):
 params={'advertiser_id':c.client.account,'data_topic':'OVERALL_ROI_PRODUCT_PROJECT','dimensions':['ad_id','stat_time_day'] if kind=='plan' else ['stat_time_day'],
 'metrics':list(FIELDS.values()),'filters':[],'order_by':[],'start_time':start+' 00:00:00','end_time':end+' 23:59:59','page_size':100}
 pages=[]
 for page in range(1,101):
  r=c.get(ReadClient.REPORT,dict(params,page=page));pages.append(r)
  if page>=max(1,r['data']['page_info']['total_page']):break
 raw={'params':params,'pages_complete':True,'pages':pages,'fetched_at':datetime.now(CHINA).isoformat()}
 return raw,normalize(complete_pages(raw,'rows'),kind,start,end)

def ensure(home,source,account,now=None):
 home=Path(home);home.mkdir(parents=True,exist_ok=True);os.umask(0o077);now=now or datetime.now(CHINA);end=now.date()-timedelta(days=1)
 latest=home/'latest.json';attempts=1;begin=floor_day(account,end-timedelta(days=44))
 if latest.exists():
  old=json.loads(latest.read_text())
  if old.get('collection_scope')==VERSION and old.get('account_id')==account and old.get('target_day')==str(end):
   folder=Path(old['evidence']).resolve()
   if not folder.is_relative_to(home.resolve()):raise ValueError('commerce_evidence_path')
   for name,sha in old.get('evidence_sha256',{}).items():
    f=folder/name
    if not f.resolve().is_relative_to(folder) or hashlib.sha256(f.read_bytes()).hexdigest()!=sha:raise ValueError('commerce_evidence_changed')
   if json.loads((folder/'result.json').read_text())!=old:raise ValueError('commerce_result_changed')
   transient=old.get('code')=='transport_or_json' or old.get('category')=='rate_limited' or old.get('http')==429 or (isinstance(old.get('http'),int) and old['http']>=500)
   attempts=old.get('attempts',1)+1
   if old.get('status')=='ok' or not transient or attempts>3 or (now-datetime.fromisoformat(old['at'])).total_seconds()<3600:return old
 folder=home/now.strftime('batch-%Y%m%dT%H%M%S%f');folder.mkdir(mode=0o700)
 result={'collection_scope':VERSION,'account_id':account,'target_day':str(end),'at':now.isoformat(),'status':'failed','attempts':attempts,'rows':[],'evidence':str(folder),'budget_eligibility_unchanged':True}
 c=Paced(ReadClient.from_runtime(source))
 if c.client.account!=account:raise ValueError('account_mismatch')
 try:
  config=c.get(ReadClient.CONFIG,{'advertiser_id':account,'data_topics':['OVERALL_ROI_PRODUCT_PROJECT']});write(folder/'config.json',config)
  configs=[x for x in config['data']['custom_config_datas'] if x['data_topic']=='OVERALL_ROI_PRODUCT_PROJECT']
  if len(configs)!=1 or not set(FIELDS.values()).issubset({m['field'] for m in configs[0]['metrics']}):raise ValueError('required_metrics_unavailable')
  plans=[];totals=[];start=begin
  while start<=end:
   stop=min(start+timedelta(days=29),end);groups={}
   for kind in ('plan','day','repeat'):
    raw,rows=fetch(c,str(start),str(stop),'plan' if kind=='repeat' else kind);write(folder/(str(start)+'-'+kind+'.json'),raw);groups[kind]=rows
   order=lambda rs:sorted(rs,key=lambda r:(r['day'],r['plan_id'] or ''))
   if order(groups['plan'])!=order(groups['repeat']):raise ValueError('repeat_revision_unresolved')
   plans+=groups['plan'];totals+=groups['day'];start=stop+timedelta(days=1)
  reconciliation=compare(plans,totals)
  expected={str(end-timedelta(days=i)) for i in range((end-begin).days+1)}
  if set(reconciliation)!=expected or not all(reconciliation.values()):raise ValueError('settlement_reconciliation_failed')
  result.update(status='ok',rows=plans,reconciliation=reconciliation,requested_start=str(begin),requested_end=str(end))
 except Exception as e:result.update(error_type=type(e).__name__,code=getattr(e,'code',None),http=getattr(e,'http',None),request_id=getattr(e,'request_id',None),category=getattr(e,'category',None))
 write(folder/'receipts.json',c.receipts)
 result['evidence_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.json')}
 write(folder/'result.json',result);atomic(latest,result);return result

def attach(plan,result,as_of_day):
 """Add observation evidence only; never fill missing base days or change eligibility."""
 view={'status':'unavailable','evidence':result.get('evidence'),'actual_profit_verified':False,'days':[],'missing_days':[]}
 if result.get('status')!='ok':return view
 rows=[r for r in result['rows'] if r['plan_id']==plan['plan_id']];base={r['day']:r for r in plan.get('daily',[])}
 conflicts=[]
 for r in rows:
  b=base.get(r['day'])
  if b and any(Decimal(str(b[k]))!=Decimal(r[k]) for k in ('spend','gmv','orders')):conflicts.append(r['day'])
  if result.get('reconciliation',{}).get(r['day']) is True:view['days'].append(settlement_view(r,as_of_day))
 end=date.fromisoformat(as_of_day)-timedelta(days=1);present={r['day'] for r in rows}
 view['missing_days']=[str(end-timedelta(days=i)) for i in range(7) if str(end-timedelta(days=i)) not in present]
 view.update(status='revision_conflict' if conflicts else 'ok',base_revision_days=conflicts)
 return view
