"""Reproducible admission from hashed raw responses, never trust normalized flags."""
import hashlib,json
from decimal import Decimal
from .history import first_enabled,parse_daily,reconcile

def derive(files):
    identities={};daily=[];totals=[];accounts=set();proofs=[]
    def read(name):
        raw=files[name];sha=hashlib.sha256(raw).hexdigest();proofs.append({'file':name,'sha256':sha});return json.loads(raw),sha
    for name in sorted(files):
        if name.startswith('logs-') and name.endswith('.json'):
            b,sha=read(name);accounts.add(b['account_id']);value=first_enabled(b)
            value['evidence']=name+':sha256:'+sha
            if b['plan_id'] in identities:raise ValueError('duplicate_plan_identity')
            identities[b['plan_id']]=value
        elif name.startswith('daily-') and name.endswith('-plan.json'):
            b,sha=read(name);accounts.add(b['account_id']);rows=parse_daily(b)
            for row in rows:row['evidence']=name+':sha256:'+sha
            daily.extend(rows)
        elif name.startswith('daily-') and name.endswith('-day.json'):
            b,sha=read(name);accounts.add(b['account_id']);totals.extend(parse_daily(b))
    if len(accounts)!=1:raise ValueError('missing_or_mixed_evidence_accounts')
    if len({(r['plan_id'],r['day']) for r in daily})!=len(daily):raise ValueError('overlapping_plan_days')
    if len({r['day'] for r in totals})!=len(totals):raise ValueError('overlapping_daily_totals')
    matched=reconcile(daily,totals)
    for row in daily:row['complete']=matched.get(row['day'],False);row['daily_aggregate_matches']=row['complete']
    revisions=[]
    if daily:
        if 'overlap-check.json' not in files:raise ValueError('revision_evidence_missing')
        b,sha=read('overlap-check.json')
        if b['account_id'] not in accounts or b['granularity']!='plan':raise ValueError('revision_scope_mismatch')
        overlap={(r['plan_id'],r['day']):r for r in parse_daily(b)}
        current={(r['plan_id'],r['day']):r for r in daily}
        keys={k for k in current if b['requested_start']<=k[1]<=b['requested_end']}|set(overlap)
        for key in sorted(keys):
            a,z=current.get(key),overlap.get(key)
            if not a or not z or any(Decimal(str(a[f]))!=Decimal(str(z[f])) for f in ('spend','gmv','orders')):
                revisions.append({'plan_id':key[0],'day':key[1]})
                if a:a['complete']=False;a['revision_unresolved']=True
    return {'account_id':accounts.pop(),'first_enabled':identities,'daily':daily,'daily_reconciliation':matched,
            'revisions':revisions,'issues':[],'evidence_files':proofs}

DERIVED_FIELDS=('account_id','first_enabled','daily','daily_reconciliation','revisions','issues')

def verify_normalized(h,files):
    if not h.get('evidence_files'):raise ValueError('evidence_manifest_empty')
    expected=derive(files)
    for key in DERIVED_FIELDS:
        if expected[key]!=h.get(key):raise ValueError('normalized_evidence_mismatch:'+key)
    needed={p['file'] for p in expected['evidence_files']}
    if not needed.issubset({p['file'] for p in h['evidence_files']}):raise ValueError('missing_evidence_dependency')
