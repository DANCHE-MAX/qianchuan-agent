"""Read-only product detail. Parent plans remain the sole aggregation authority."""
from pathlib import Path
from decimal import Decimal
import json,hashlib
GMV='total_pay_order_gmv_include_coupon_for_roi2'
NOTE='商品明细仅解释计划内GMV和广告消耗，不重复计入利润、合计或排序；款号待核验。'

def amount(x):
 if x is None:return None
 d=Decimal(str(x))
 if not d.is_finite() or d<0:raise ValueError('Invalid product amount')
 return d

def view(report,pid,products,returned):
 parents=[r for r in report['rows'] if str(r['plan_id'])==str(pid)]
 if len(parents)!=1:raise ValueError('Ambiguous parent')
 parent=parents[0];ids=[str(p['id']) for p in products]
 if len(ids)!=len(set(ids)):raise ValueError('Duplicate product binding')
 known={}
 for r in returned:
  k=str(r['product_id'])
  if r['date']!=report['date'] or k not in ids or k in known:raise ValueError('Invalid product scope')
  known[k]=r
 rows=[]
 for p in products:
  r=known.get(str(p['id']),{});g=amount(r.get('gmv'));s=amount(r.get('spend'))
  rows.append(dict(id=str(p['id']),name=p['name'],gmv=None if g is None else f'{g:.2f}',spend=None if s is None else f'{s:.2f}',sku_status='待核验',reason='' if g is not None and s is not None else '接口未返回完整商品日GMV和消耗，待核验；不能补零'))
 missing=sum(r['gmv'] is None or r['spend'] is None for r in rows);status='partial' if missing else 'complete'
 out=dict(date=report['date'],plan_id=str(pid),name=parent.get('plan_name') or parent['name'],products=rows,missing=missing,included_in_totals=False,note=NOTE)
 for k in ('gmv','spend'):
  vals=[Decimal(r[k]) for r in rows if r[k] is not None];total=sum(vals,Decimal(0)) if vals else None;control=amount(parent.get(k))
  out['known_'+k]=None if total is None else f'{total:.2f}';out['plan_'+k]=None if control is None else str(control)
  if control is None or total is None:
   if status!='mismatch':status='pending'
  elif total>control+Decimal('.01') or (not missing and abs(total-control)>Decimal('.01')):status='mismatch'
 out['status']=status
 return out

def validate(report,views):
 seen=set()
 for v in views:
  if v['plan_id'] in seen:raise ValueError('Duplicate breakdown plan')
  seen.add(v['plan_id'])
  rebuilt=view(report,v['plan_id'],v['products'],[dict(date=v['date'],product_id=p['id'],gmv=p['gmv'],spend=p['spend']) for p in v['products']])
  for key in ('date','missing','included_in_totals','known_gmv','known_spend','plan_gmv','plan_spend','status'):
   if v[key]!=rebuilt[key]:raise ValueError('Product breakdown changed: '+key)
  if v['status']=='mismatch':raise ValueError('Product and plan totals conflict')

def normalize(rows,pid):
 out=[]
 for r in rows:
  d=r['dimensions'];m=r['metrics']
  if str(d['ad_id']['Value'])!=str(pid):raise ValueError('Wrong plan in product response')
  out.append(dict(date=d['stat_time_day']['ValueStr'][:10],product_id=str(d['qianchuan_product_id']['Value']),gmv=m.get(GMV,{}).get('Value'),spend=m.get('stat_cost_for_roi2',{}).get('Value')))
 return out

def binding_pending(root,report):
 if not report:return []
 if 'product_binding_pending' in report:return report['product_binding_pending']
 try:
  entries=json.loads((Path(root)/'data/product_identity/pending.json').read_text())
  return [dict(plan_id=e['plan_id'],name=e['name'],reason=e['reason']) for e in entries if not e.get('products') and any(str(r['plan_id'])==str(e['plan_id']) for r in report['rows'])]
 except (OSError,ValueError,KeyError):return []

def historical(root,report):
 if not report:return []
 if 'product_breakdowns' in report:return report['product_breakdowns']
 path=Path(root)/'data/product_identity/multi-product-supplement.json'
 if not path.exists():return []
 try:
  s=json.loads(path.read_text())
  if str(s['account'])!=str(report['account']) or not s['range'][0]<=report['date']<=s['range'][1]:return []
  evidence=s['evidence'];raw=None
  for name,h in evidence.items():
   p=Path(name)
   if hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('Changed source evidence')
   if p.name=='products-diagnose.json':raw=json.loads(p.read_text())
  if raw is None:raise ValueError('Missing product source')
  req=raw['request'];resp=raw['response'];data=resp['data'];pi=data['page_info']
  if str(req['advertiser_id'])!=str(report['account']) or resp['code']!=0 or pi['total_page']!=1 or pi['total_number']!=len(data['rows']):raise ValueError('Invalid source scope')
  rows=[r for r in normalize(data['rows'],s['plan_id']) if r['date']==report['date']]
  return [view(report,s['plan_id'],s['products'],rows)]
 except (OSError,ValueError,KeyError,TypeError):return []

def capture(root,env,report,evidence,request=None):
 from . import profit_daily as p
 pending=Path(root)/'data/product_identity/pending.json'
 if not pending.exists():return report
 candidates=json.loads(pending.read_text());views=[];bindings=[]
 for entry in candidates:
  pid=str(entry['plan_id'])
  if not any(str(r['plan_id'])==pid for r in report['rows']):continue
  if not entry.get('products'):
   bindings.append(dict(plan_id=pid,name=entry['name'],reason='直播商品绑定待核验：计划详情未提供商品绑定；现有直播报表无商品维度，不推断归属。'));continue
  products=[];rows=[];reason=None
  try:
   found=[]
   for file in Path(evidence).glob('VIDEO_PROM_GOODS-*.json'):
    doc=json.loads(file.read_text());req=doc.get('request',{})
    if str(req.get('advertiser_id'))!=str(report['account']) or req.get('start_time')!=report['date']+' 00:00:00' or req.get('end_time')!=report['date']+' 23:59:59':continue
    for ad in doc.get('response',{}).get('data',{}).get('ad_list',[]):
     if str(ad.get('ad_info',{}).get('id'))==pid:found.append(ad.get('product_info') or [])
   if not found or any(x!=found[0] for x in found):raise ValueError('当日计划商品绑定缺失或冲突')
   products=[dict(id=str(x['product_id']),name=x['product_name']) for x in found[0]]
   if not products:raise ValueError('当日计划未返回商品绑定')
   args=dict(advertiser_id=report['account'],start_time=report['date']+' 00:00:00',end_time=report['date']+' 23:59:59',data_topic='OVERALL_ROI_PRODUCT_PRODUCT',dimensions=['ad_id','qianchuan_product_id','qianchuan_product_name','stat_time_day'],metrics=['stat_cost_for_roi2',GMV],filters=[dict(field='ad_id',operator=7,values=[pid])],order_by=[])
   raw,_=p.pages(env,p.REPORT,args,Path(evidence),'product-breakdown-'+pid,request or p.get);rows=normalize(raw,pid)
   v=view(report,pid,products,rows)
  except (OSError,ValueError,KeyError,TypeError,TimeoutError) as exc:
   reason='商品级读取或绑定证据不足，待核验（'+type(exc).__name__+'）；不沿用其他日期数据'
   v=view(report,pid,products,[])
  if reason:v['reason']=reason
  views.append(v)
 report['product_breakdowns']=views;report['product_binding_pending']=bindings
 if views or bindings:report.setdefault('warnings',[]).append('多商品计划明细见Excel：缺失继续待核验，计划与商品仅计一次；直播绑定待核验。')
 return report
