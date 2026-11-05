"""Bounded daily GET recovery audit. No changes to canonical history or budget gates."""
import json,hashlib,os
from pathlib import Path
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from .plan_audit import audit_plan
from .daily_refresh import acquire,Paced,write,atomic
from .history import parse_daily,reconcile
from .read_transport import ReadClient
from .collection_scope import ACCOUNT,START

def requested_gaps(plans,as_of,account=None):
 return {p['plan_id']:sorted(set(a['missing_daily_dates'] or a['recent_missing_dates'])) for p in plans if (a:=audit_plan(p,as_of,[],collection_start=str(START) if account==ACCOUNT else None))['missing_daily_dates'] or a['recent_missing_dates']}
def classify(gaps,rows,reconciliation,evidence):
 found={(r['plan_id'],r['day']):r for r in rows};result={}
 for pid,dates in gaps.items():
  result[pid]=[]
  for day in dates:
   r=found.get((pid,day));item={'day':day,'status':'not_returned' if r is None else 'unreconciled','evidence':evidence,'budget_clearance':False}
   if r and reconciliation.get(day):item.update(status='recovered_shadow_only',row=dict(r,complete=True,daily_aggregate_matches=True,evidence=evidence))
   result[pid].append(item)
 return result

def retry_allowed(result,now):
 transient=result.get('code')=='transport_or_json' or result.get('category')=='rate_limited' or result.get('http')==429 or (isinstance(result.get('http'),int) and result['http']>=500)
 return bool(result.get('status')=='failed' and transient and result.get('attempts',1)<3 and (now-datetime.fromisoformat(result['at'])).total_seconds()>=3600)

def choose_windows(dates,today,limit=6):
 start=date.fromisoformat(min(dates));end=date.fromisoformat(max(dates));windows=[]
 while start<=end:
  stop=min(start+timedelta(days=29),end)
  if any(str(start)<=d<=str(stop) for d in dates):windows.append((start,stop))
  start=stop+timedelta(days=1)
 if len(windows)<=limit:return windows
 older=windows[:-1];offset=(today.toordinal()*(limit-1))%len(older)
 selected=[older[(offset+i)%len(older)] for i in range(limit-1)]+[windows[-1]]
 return sorted(selected)

def ensure(home,source,snapshot):
 home=Path(home);home.mkdir(parents=True,exist_ok=True);os.umask(0o077);account=snapshot['account_id'];gaps=requested_gaps([p for p in snapshot['plans'] if p.get('scope')=='chuanfang_goods'],snapshot['as_of'],account)
 day=snapshot['as_of'][:10];key=hashlib.sha256(json.dumps([account,day,gaps],sort_keys=True).encode()).hexdigest();folder=home/key;pointer=home/(key+'-attempt.json');now=datetime.now(ZoneInfo('Asia/Shanghai'));attempts=1
 if pointer.exists():
  folder=Path(json.loads(pointer.read_text())['folder'])
  if not folder.resolve().is_relative_to(home.resolve()):raise ValueError('recovery_pointer_path')
 output=folder/'result.json'
 if output.exists():
  result=json.loads(output.read_text())
  for name,sha in result['evidence_sha256'].items():
   path=(folder/name).resolve()
   if not path.is_relative_to(folder.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('recovery_evidence_changed')
  if not retry_allowed(result,now):return result
  attempts=result.get('attempts',1)+1;folder=home/(key+'-'+str(attempts));output=folder/'result.json'
 folder.mkdir(mode=0o700,exist_ok=True);now=datetime.now(ZoneInfo('Asia/Shanghai'));result={'account_id':account,'at':now.isoformat(),'requested_gaps':gaps,'status':'ok','attempts':attempts,'plans':{},'recovered_count':0,'not_returned_count':0,'budget_gate_unchanged':True}
 if not gaps:write(output,dict(result,evidence_sha256={}));return result
 c=Paced(ReadClient.from_runtime(source))
 if c.client.account!=account:raise ValueError('recovery_account_mismatch')
 dates=sorted({d for ds in gaps.values() for d in ds});start=date.fromisoformat(dates[0]);end=date.fromisoformat(dates[-1]);all_rows=[];checks={};queried=set();windows=choose_windows(dates,date.fromisoformat(day))
 try:
  for start,stop in windows:
   groups={}
   for kind in ('plan','day','repeat'):
    bundle=acquire(c,start,stop,'plan' if kind=='repeat' else kind,now);write(folder/(str(start)+'-'+kind+'.json'),bundle);groups[kind]=parse_daily(bundle)
   canonical=lambda rs:sorted([(r['plan_id'],r['day'],r['spend'],r['gmv'],r['orders']) for r in rs])
   if canonical(groups['plan'])!=canonical(groups['repeat']):raise ValueError('recovery_repeat_revision')
   checks.update(reconcile(groups['plan'],groups['day']));all_rows+=groups['plan'];queried.update(str(start+timedelta(days=i)) for i in range((stop-start).days+1))
  result['plans']=classify(gaps,all_rows,checks,str(folder));result['checked_ranges']=[[str(a),str(b)] for a,b in windows]
  for entries in result['plans'].values():
   for item in entries:
    if item['day'] not in queried:item['status']='not_queried_request_cap'
  result['recovered_count']=sum(x['status']=='recovered_shadow_only' for rs in result['plans'].values() for x in rs)
  result['not_returned_count']=sum(x['status']=='not_returned' for rs in result['plans'].values() for x in rs)
 except Exception as e:result.update(status='failed',error_type=type(e).__name__,code=getattr(e,'code',None),http=getattr(e,'http',None),request_id=getattr(e,'request_id',None),category=getattr(e,'category',None))
 write(folder/'receipts.json',c.receipts);result['evidence_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.json')};write(output,result);atomic(pointer,{'folder':str(folder)});atomic(home/'latest.json',dict(result,result_file=str(output)));return result
