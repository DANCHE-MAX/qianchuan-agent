"""Enrich read-only projections from pinned local acquired evidence."""
import hashlib,json
from pathlib import Path
from .history import at

def enrich(result,history_path):
    path=Path(history_path).resolve();raw=path.read_bytes();h=json.loads(raw)
    if h.get('version') not in ('source-history-v2','source-history-v3') or h.get('account_id')!=result['account_id']:raise ValueError('history_account_or_version_mismatch')
    if at(h['built_at'])>at(result['as_of']):raise ValueError('history_from_future')
    files={}
    for proof in h['evidence_files']:
        p=(path.parent/proof['file']).resolve()
        if not p.is_relative_to(path.parent) or hashlib.sha256(p.read_bytes()).hexdigest()!=proof['sha256']:raise ValueError('history_evidence_changed')
        if proof['file'] in files:raise ValueError('duplicate_evidence_file')
        files[proof['file']]=p.read_bytes()
    from .history_build import verify_normalized
    verify_normalized(h,files)
    plans={p['plan_id']:p for p in result['plans']}
    scope_ids=set(h.get('first_enabled',{}))|{r['plan_id'] for r in h['daily']}
    for pid in scope_ids:
        if pid not in plans:
            plans[pid]={'plan_id':pid,'scope':'chuanfang_goods','first_enabled_on':None,'daily':[],
                'observations':[],'hourly_observations':[],'status':None,'budget':None,'refund':{},
                'missing':['first_enabled_on','complete_daily_facts','fixed_budget_baseline','budget_change_history','state_events']}
        p=plans[pid];p['scope']='chuanfang_goods'
        identity=h.get('first_enabled',{}).get(pid)
        if identity and identity.get('verified'):
            p['first_enabled_on']=at(identity['first_enabled_at']).date().isoformat();p['first_enabled_evidence']=identity['evidence'];p['first_enabled_details']=identity
            p['missing']=[m for m in p['missing'] if m!='first_enabled_on']
        p['daily']=[r for r in h['daily'] if r['plan_id']==pid]
        for r in p['daily']:
            if r['account_id']!=result['account_id'] or at(r['reported_at'])>at(result['as_of']):raise ValueError('daily_account_or_time_mismatch')
        if p['daily']:
            p['missing']=[m for m in p['missing'] if m!='complete_daily_facts'];p['missing'].append('daily_absent_rows_remain_unknown')
        verified=h.get('verified_refund_mappings',{}).get(pid)
        approved_basis='用户指定：托管采用季节整体退款率，不拆商品结构'
        if (verified and (p.get('refund') or {}).get('basis')==approved_basis and verified.get('basis')==approved_basis
                and verified['source_config_sha256']==result['source_fingerprint'].get('config/rules.json')):
            if all(str((p.get('refund') or {}).get(k))==str(verified[k]) for k in ('rate','band','model_version','season')):
                p['refund']['verified']=True;p['refund']['verification_evidence']=verified['evidence']
    result['plans']=[plans[k] for k in sorted(plans)]
    result['history_source']={'sha256':hashlib.sha256(raw).hexdigest(),'built_at':h['built_at'],
        'first_enabled_verified':sum(bool(p.get('first_enabled_on')) for p in plans.values()),
        'admitted_plan_days':sum(r['complete'] for r in h['daily']),
        'reconciliation':h.get('daily_reconciliation',{}),'revisions':h.get('revisions',[]),'issues':h.get('issues',[])}
    result['gaps']=[g for g in result['gaps'] if g not in ('first_enabled_source_missing','complete_daily_orders_unverified')]
    if h.get('revision_events_pending_execution_review'):
        result['gaps'].append('data_revision_pending_execution_review')
        result['pending_execution_revisions']=h['revision_events_pending_execution_review']
    result['gaps']+=['first_enabled_partial_coverage','daily_absent_rows_remain_unknown','historical_lifecycle_state_unverified']
    return result
