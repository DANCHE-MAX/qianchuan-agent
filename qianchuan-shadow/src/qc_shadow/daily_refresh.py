"""Append-only daily GET acquisition; atomic promotion only after reconciliation/revision checks."""
import json,hashlib,shutil,time,fcntl,os
from pathlib import Path
from datetime import datetime,date,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from .read_transport import ReadClient,ReadFailure
from .history_build import derive,verify_normalized
from .collection_scope import ACCOUNT,START,VERSION,floor_day,scoped_changes

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2));path.chmod(0o600)
def atomic(path,value):
    temp=path.with_suffix('.tmp');write(temp,value);temp.replace(path)
def changes(old,new):
    a={(r['plan_id'],r['day']):r for r in old};b={(r['plan_id'],r['day']):r for r in new}
    old_end=max((r['day'] for r in old),default='')
    return [{'plan_id':k[0],'day':k[1],'before':a.get(k),'after':b.get(k)} for k in sorted(set(a)|set(b))
            if k[1]<=old_end and (k not in a or k not in b or any(Decimal(str(a[k][m]))!=Decimal(str(b[k][m])) for m in ('spend','gmv','orders')))]
def retain_revisions(prior,current):
    records={hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest():r for r in prior}
    for r in current:records.setdefault(hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest(),r)
    return list(records.values())

class Paced:
    def __init__(self,client,sleep=time.sleep):self.client=client;self.sleep=sleep;self.receipts=[]
    def get(self,path,params):
        for attempt in range(3):
            self.sleep(4 if attempt==0 else (8 if attempt==1 else 20))
            try:
                result=self.client.get(path,params);self.receipts.append(dict(endpoint=path,attempt=attempt+1,code=0,request_id=result.get('request_id')));return result
            except ReadFailure as e:
                self.receipts.append(dict(endpoint=path,attempt=attempt+1,code=e.code,http=e.http,request_id=e.request_id,category=e.category))
                if e.category!='rate_limited' or attempt==2:raise

def acquire(client,start,end,kind,now):
    params={'advertiser_id':client.client.account,'data_topic':'OVERALL_ROI_PRODUCT_PROJECT','dimensions':['ad_id','ad_name','stat_time_day'] if kind=='plan' else ['stat_time_day'],
        'metrics':['stat_cost_for_roi2','total_pay_order_gmv_include_coupon_for_roi2','total_pay_order_count_for_roi2'],'filters':[],'order_by':[],
        'start_time':str(start)+' 00:00:00','end_time':str(end)+' 23:59:59','page_size':100}
    pages=[];expected=None
    for page in range(1,101):
        r=client.get(ReadClient.REPORT,dict(params,page=page));pages.append(r);info=r['data']['page_info'];pair=(info['total_number'],info['total_page'])
        if expected is None:expected=pair
        if expected!=pair or info['page']!=page:raise ValueError('pagination_changed')
        if page>=max(1,info['total_page']):
            if sum(len(p['data']['rows']) for p in pages)!=info['total_number']:raise ValueError('pagination_count_mismatch')
            return {'account_id':client.client.account,'requested_start':str(start),'requested_end':str(end),'granularity':kind,'params':params,
                'fetched_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'pages_complete':True,'pages':pages}
    raise ValueError('page_limit')

def ensure(home,source_root,seed,now=None,force=False):
    home=Path(home).resolve();source_root=Path(source_root).resolve();seed=Path(seed).resolve()
    if home==source_root or home.is_relative_to(source_root):raise ValueError('separate_daily_directory_required')
    home.mkdir(parents=True,exist_ok=True);os.umask(0o077);now=now or datetime.now(ZoneInfo('Asia/Shanghai'));end=now.date()-timedelta(days=1)
    state_path=home/'daily_status.json';pointer=home/'active_history.json'
    with (home/'daily.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'busy','history':None}
        state=json.loads(state_path.read_text()) if state_path.exists() else {}
        if not force and state.get('collection_scope')==VERSION and state.get('status')=='ok' and state.get('target_day')==str(end) and pointer.exists():
            return dict(state,history=json.loads(pointer.read_text())['history'])
        if not force and (now.hour,now.minute)<(9,45):return {'status':'waiting_daily_refresh','history':None,'target_day':str(end)}
        if not force and state.get('collection_scope')==VERSION and state.get('target_day')==str(end) and state.get('status')!='ok':
            if state.get('status')=='revision_review_required' or state.get('attempts',0)>=3:return dict(state,history=None)
            if (now-datetime.fromisoformat(state['checked_at'])).total_seconds()<3600:return dict(state,history=None)
        attempts=state.get('attempts',0)+1 if state.get('collection_scope')==VERSION and state.get('target_day')==str(end) else 1
        base=Path(json.loads(pointer.read_text())['history']) if pointer.exists() else seed
        out=home/('batch-'+now.strftime('%Y%m%dT%H%M%S%f'));out.mkdir(mode=0o700)
        client=Paced(ReadClient.from_runtime(source_root));status={'target_day':str(end),'checked_at':now.isoformat(),'attempts':attempts,'batch':str(out),'execution_allowed':False,'plan_pause_enabled':False,'collection_scope':VERSION}
        try:
            old=json.loads(base.read_text());raws={r['file']:(base.parent/r['file']).read_bytes() for r in old['evidence_files']}
            if any(hashlib.sha256(raws[r['file']]).hexdigest()!=r['sha256'] for r in old['evidence_files']):raise ValueError('prior_evidence_changed')
            verify_normalized(old,raws)
            if old['account_id']!=client.client.account:raise ValueError('account_changed')
            cutoff=floor_day(client.client.account,end-timedelta(days=6));starts=[]
            for name,raw in raws.items():
                if name.startswith('logs-'): (out/name).write_bytes(raw)
                elif name.startswith('daily-'):
                    if client.client.account==ACCOUNT and old.get('collection_scope')!=VERSION:continue
                    b=json.loads(raw)
                    if client.client.account==ACCOUNT and date.fromisoformat(b['requested_start'])<START:continue
                    if date.fromisoformat(b['requested_end'])<cutoff:(out/name).write_bytes(raw)
                    else:starts.append(date.fromisoformat(b['requested_start']))
            start=min(starts) if starts else date.fromisoformat(max(r['day'] for r in old['daily']))+timedelta(days=1)
            if client.client.account==ACCOUNT and old.get('collection_scope')!=VERSION:start=START
            start=floor_day(client.client.account,start)
            while start<=end:
                stop=min(start+timedelta(days=29),end)
                for kind in ('plan','day'):write(out/('daily-'+str(start)+'-'+kind+'.json'),acquire(client,start,stop,kind,now))
                start=stop+timedelta(days=1)
            write(out/'overlap-check.json',acquire(client,cutoff,end,'plan',now))
            h=derive({p.name:p.read_bytes() for p in out.glob('*.json')})
            if h['revisions'] or not all(h['daily_reconciliation'].values()) or not h['daily_reconciliation'].get(str(end)):raise ValueError('daily_or_repeat_reconciliation_failed')
            revised=scoped_changes(old['daily'],h['daily'],client.client.account);write(out/'revision-comparison.json',revised)
            revision_file=home/'revision_history.json'
            prior_revisions=json.loads(revision_file.read_text()) if revision_file.exists() else []
            revision_history=retain_revisions(retain_revisions(old.get('revision_events_pending_execution_review',[]),prior_revisions),revised)
            atomic(revision_file,revision_history)
            cfgraw=(source_root/'config/rules.json').read_bytes();cfg=json.loads(cfgraw)['model'];verified={}
            for pid,r in cfg.get('plans',{}).items():
                if r.get('basis')=='用户指定：托管采用季节整体退款率，不拆商品结构':
                    band=r['refund_band'];verified[pid]={'band':band,'rate':str(cfg['refund_rates'][cfg['season']][band]),'model_version':cfg['version'],'season':cfg['season'],
                        'source_config_sha256':hashlib.sha256(cfgraw).hexdigest(),'evidence':'deployed_config_explicit_user_mapping:'+pid,'basis':r['basis']}
            h['revision_events_pending_execution_review']=revision_history
            h.update(collection_scope=VERSION,collection_start_day=str(START) if client.client.account==ACCOUNT else None,version='source-history-v3',built_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),execution_enabled=False,verified_refund_mappings=verified)
            normalized=out/'normalized-history.json';write(normalized,h)
            status.update(status='ok',plan_days=len(h['daily']),revisions=len(revised),pending_execution_revisions=len(revision_history),history=str(normalized))
            atomic(pointer,{'history':str(normalized)});atomic(state_path,status);return status
        except Exception as exc:
            status.update(status='failed',error_type=type(exc).__name__,reason=str(exc) if isinstance(exc,(ReadFailure,ValueError)) else 'daily_refresh_failed');atomic(state_path,status);return dict(status,history=None)
        finally:write(out/'request-receipts.json',client.receipts)
