"""Generate supplemental evidence and explicitly conditional shadow results, not execution inputs."""
import json,hashlib
from decimal import Decimal
from pathlib import Path
from qc_shadow.readiness import budget_fact,budget_events,product_fact,pagination_audit,conditional_daily,load_evidence
from qc_shadow.daily_rules import assess_daily
folder=Path('evidence/readiness-v3/private');snapshot_path=Path('evidence/live-readiness-shadow-v3.json');manifest={};snapshot=load_evidence(snapshot_path,manifest)
s=snapshot['input']['snapshots'][0];plans={p['plan_id']:p for p in s['plans']};account=s['account_id'];facts=[]
def read(p):
 return load_evidence(p,manifest)
for path in sorted(folder.glob('detail-*.json')):
 d=read(path);pid=path.stem.removeprefix('detail-')
 if str(d['params']['advertiser_id'])!=account or str(d['params']['ad_id'])!=pid or d['response'].get('code')!=0:raise ValueError('detail_identity_or_response')
 data=d['response']['data']
 if str(data['ad_id'])!=pid:raise ValueError('detail_plan_mismatch')
 log=read(folder/('logs-'+pid+'.json'))
 if log['account_id']!=account or log['plan_id']!=pid:raise ValueError('log_identity')
 rows=[r for page in log['pages'] for r in page['data']['logs']]
 events=budget_events(rows,pid);budget=budget_fact(data.get('delivery_setting',{}))
 budget.update(observed_at=d['fetched_at'],events=events,log_query_start=log['query_start'],log_query_end=log['query_end'],capture_reached_last_page=log['pages_complete'],pagination= pagination_audit(log['pages']),
               latest_parsed_log_matches_current=(events[-1]['after'] is not None and Decimal(events[-1]['after'])==Decimal(budget['current'])) if events and budget['current'] else None,
               note='完整分页仅覆盖查询窗口；解析的日预算事件不是全生命周期或并发一致性证明。当前预算仅候选基准。')
 products=[]
 for binding in data.get('product_infos',[]):
  product_id=str(binding['product_id']);p=read(folder/('product-'+product_id+'.json'))
  if str(p['params']['advertiser_id'])!=account or p['params']['filter']['product_ids']!=[int(product_id)]:raise ValueError('product_binding')
  fact=product_fact(p['response'],product_id);fact['fetched_at']=p['fetched_at'];products.append(fact)
 original=plans.get(pid,{})
 refund=original.get('refund') or {};conditional=None
 if refund.get('rate') and not refund.get('verified'):
  conditional={'simulation':True,'assumption':'既有待核退款档位适用于全部被测日期；尚未证实，不升级真实判断','execution_allowed':False,
               'result':conditional_daily(original.get('daily',[]),s['as_of'],refund)}
 facts.append({'plan_id':pid,'budget':budget,'products':products,'refund':refund,'conditional_shadow':conditional,
               'first_enabled_on':original.get('first_enabled_on'),'status':data.get('status'),'opt_status':data.get('opt_status'),
               'full_execution':'blocked','rule_live_on':None,'execution_allowed':False})
result={'version':'readiness-v3','account_id':account,'as_of':s['as_of'],'shadow_run_id':snapshot['run_id'],'execution_allowed':False,
        'purpose':'补充证据及条件模拟；不进入正式规则输入，不冻结B，不晋升退款映射','manifest_sha256':manifest,'plans':facts}
out=folder/'readiness-v3.json'
with out.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
out.chmod(0o600)
summary={'plans':len(facts),'products':len(list(folder.glob('product-*.json'))),'budget_distribution':{},'verified_refunds':sum(bool(p['refund'].get('verified')) for p in facts),'conditional_refunds':sum(bool(p['conditional_shadow']) for p in facts),'shadow_run_id':snapshot['run_id'],'execution_allowed':False}
for p in facts:
 b=p['budget']['current'];summary['budget_distribution'][b]=summary['budget_distribution'].get(b,0)+1
print(json.dumps(summary,ensure_ascii=False))
Path('evidence/readiness-v3/summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
for p in facts:
 print('tail',p['plan_id'][-4:],'budget',p['budget']['current'],'events',len(p['budget']['events']),'match',p['budget']['latest_parsed_log_matches_current'],'products',len(p['products']),'prices',[(r.get('raw_prices') or {}).get('discount_lower_price') for r in p['products']],'conditional',p['conditional_shadow']['result'] if p['conditional_shadow'] else None)
