"""Offline, versioned contribution-profit module. No network or execution adapter."""
import hashlib,json,sqlite3
from datetime import date,datetime
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path

VERSION='order-profit-shadow-v3'
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def number(v):
    if v is None or isinstance(v,bool):raise ValueError('missing_or_invalid_amount')
    try:r=Decimal(str(v))
    except Exception as e:raise ValueError('invalid_amount') from e
    if not r.is_finite() or r<0:raise ValueError('invalid_amount')
    return r
def cash(v):return str(v.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def recommend_spend(gmv,pre,ad_rate,target_rate):
    baseline=Decimal(cash(gmv*ad_rate));target=Decimal(cash(gmv*target_rate))
    post=pre-baseline;recommended=max(Decimal(0),min(baseline,pre))
    return {'baseline_ad_total':cash(baseline),'profit_target':cash(target),
        'alert_required':post<=target,'target_met':post>target,
        'recommended_ad_total':cash(recommended),'profit_at_recommendation':cash(pre-recommended),
        'loss_before_ads':pre<0,'recommendation_scope':'same_period_total_not_incremental',
        'recommended_additional_spend':None}

def compare_ad_costs(gmv,pre,actual,ad_rate):
    """Same-period scenarios, not permission or forecast for new advertising."""
    estimated=Decimal(cash(gmv*ad_rate))
    total=max(Decimal(0),min(estimated,pre)) if pre is not None else None
    known=pre is not None and actual is not None
    return {
        'actual_ad_spend':cash(actual) if actual is not None else None,
        'actual_ad_source':'single_product_plan_report_finance_unreconciled',
        'estimated_ad_spend':cash(estimated),
        'post_ad_profit_actual':cash(pre-actual) if known else None,
        'post_ad_profit_estimated':cash(pre-estimated) if pre is not None else None,
        'break_even_ad_total':cash(max(Decimal(0),pre)) if pre is not None else None,
        'break_even_additional_spend':cash(max(Decimal(0),pre-actual)) if known else None,
        'recommended_additional_spend':cash(max(Decimal(0),total-actual)) if known else None,
        'actual_over_estimated_ad':cash(max(Decimal(0),actual-estimated)) if actual is not None else None,
        'additional_scope':'historical_same_period_headroom_not_live_budget',
        'additional_execution_allowed':False,
    }
def validate_profile(p):
    for k in ('version','account_id','start','end','products','fees','shipping'): 
        if not p.get(k):raise ValueError('profile_missing:'+k)
    if date.fromisoformat(p['start'])>date.fromisoformat(p['end']):raise ValueError('invalid_period')
    number(p['shipping'])
    for v in p['fees'].values():
        if number(v)>1:raise ValueError('fee_out_of_range')
    for v in p['products'].values():number(v['cost'])
    if p.get('minimum_profit') is not None:number(p['minimum_profit'])
    if p.get('ad_basis','plan_spend') not in ('plan_spend','gross_gmv_rate'):raise ValueError('invalid_ad_basis')
    if p.get('return_policy','confirmed_return_without_resale_test') not in ('confirmed_return_without_resale_test','refund_success_assumed_returned'):raise ValueError('invalid_return_policy')
    if p.get('ad_basis')=='gross_gmv_rate':
        for k in ('ad_rate','profit_target_rate'):
            if number(p.get(k))>1:raise ValueError('rate_out_of_range')

class ProfitStore:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS records(kind TEXT,key TEXT,payload TEXT,as_of TEXT,version INTEGER,PRIMARY KEY(kind,key));
        CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY,kind TEXT,key TEXT,before TEXT,after TEXT,as_of TEXT,source_hash TEXT);
        CREATE TABLE IF NOT EXISTS imports(hash TEXT PRIMARY KEY,as_of TEXT);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,payload TEXT);
        ''')
        path.chmod(0o600)
    def close(self):self.db.close()
    def change_count(self):return self.db.execute('SELECT count(*) FROM changes').fetchone()[0]
    def ingest(self,rows,source_hash,as_of):
        stamp=datetime.fromisoformat(as_of)
        if stamp.tzinfo is None:raise ValueError('timezone_required')
        seen={}
        for kind,key,r in rows:
            if kind not in ('orders','refunds','returns','ads','costs','fees','meta'):raise ValueError('unknown_record_kind')
            if not isinstance(key,str) or not key:raise ValueError('invalid_key')
            if kind=='orders':
                number(r.get('income'))
                if type(r.get('quantity')) is not int or r['quantity']<=0:raise ValueError('invalid_quantity')
                for field in ('id','parent','product'):
                    if not isinstance(r.get(field),str) or not r[field]:raise ValueError('invalid_identifier')
            if kind=='ads':number(r.get('spend'))
            if kind=='returns':
                if type(r.get('quantity')) is not int or r['quantity']<0 or not r.get('evidence'):raise ValueError('invalid_return_confirmation')
            body=canonical(r)
            if (kind,key) in seen and seen[kind,key]!=body:raise ValueError('conflicting_duplicate:'+key)
            seen[kind,key]=body
        with self.db:
            if self.db.execute('SELECT 1 FROM imports WHERE hash=?',(source_hash,)).fetchone():return {'duplicate_file':True,'changed':0}
            changed=0
            for (kind,key),body in seen.items():
                old=self.db.execute('SELECT payload,as_of,version FROM records WHERE kind=? AND key=?',(kind,key)).fetchone()
                if old and stamp<datetime.fromisoformat(old[1]):raise ValueError('stale_snapshot:'+key)
                if old and old[0]==body:
                    self.db.execute('UPDATE records SET as_of=? WHERE kind=? AND key=?',(as_of,kind,key));continue
                if old and stamp==datetime.fromisoformat(old[1]):raise ValueError('same_time_conflict:'+key)
                self.db.execute('INSERT INTO changes(kind,key,before,after,as_of,source_hash) VALUES(?,?,?,?,?,?)',(kind,key,old[0] if old else None,body,as_of,source_hash))
                self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)',(kind,key,body,as_of,old[2]+1 if old else 1));changed+=1
            self.db.execute('INSERT INTO imports VALUES(?,?)',(source_hash,as_of))
        return {'duplicate_file':False,'changed':changed}
    def records(self,kind):return {k:json.loads(v) for k,v in self.db.execute('SELECT key,payload FROM records WHERE kind=? ORDER BY key',(kind,))}
    def save_run(self,result):
        body=canonical(result);key=hashlib.sha256(body.encode()).hexdigest()
        with self.db:self.db.execute('INSERT OR IGNORE INTO runs VALUES(?,?)',(key,body))
        return key

def calculate(store,p):
    validate_profile(p);orders=store.records('orders');refunds=store.records('refunds');returns=store.records('returns');ads=store.records('ads')
    details=[];issues=[];results=[]
    for key,r in returns.items():
        if r['order'] not in orders:raise ValueError('return_order_unmatched:'+key)
    for key,r in refunds.items():
        if r['order'] not in orders:issues.append({'type':'refund_order_unmatched','order':r['order']})
    for pid,product in p['products'].items():
        sums={k:Decimal(0) for k in ('gross','refund','cost','logistics','pending_return_cost')};count=0;parents=set();missing_refund=False
        for oid,r in orders.items():
            if r['product']!=pid:continue
            reason='included'
            if not r['paid']:reason='missing_payment'
            elif not p['start']<=r['paid'][:10]<=p['end']:reason='outside_period'
            if reason!='included':
                issues.append({'type':reason,'order':oid,'style':product['style'],'source':r.get('source')});continue
            count+=1;rf=refunds.get(oid);ret=returns.get(oid);qty=ret['quantity'] if ret else 0
            if qty>r['quantity'] or (qty and not r['shipped']):raise ValueError('invalid_return_quantity:'+oid)
            if rf is None:
                missing_refund=True;issues.append({'type':'missing_refund_state','order':oid,'style':product['style'],'source':r.get('source')})
            gross=number(r['income']);refunded=bool(rf and rf['success']);refund=gross if refunded else Decimal(0)
            assumed=r['shipped'] and refunded and not ret and p.get('return_policy')=='refund_success_assumed_returned'
            if assumed:
                qty=r['quantity'];issues.append({'type':'return_assumed_by_business','order':oid,'style':product['style'],'source':r.get('source'),'reversed_cost':cash(number(product['cost'])*qty)})
            cost=number(product['cost'])*(r['quantity']-qty) if r['shipped'] else Decimal(0)
            shipping=number(p['shipping']) if r['shipped'] and r['parent'] not in parents else Decimal(0)
            if r['shipped']:parents.add(r['parent'])
            if r['shipped'] and refunded and qty<r['quantity']:
                sums['pending_return_cost']+=cost;issues.append({'type':'return_confirmation_missing','order':oid,'style':product['style'],'source':r.get('source'),'retained_cost':cash(cost)})
            if rf and rf.get('status') in ('待退货','待收退货','换货成功'):
                issues.append({'type':'after_sale_pending' if rf['status']!='换货成功' else 'exchange_extra_cost_unknown','order':oid,'style':product['style'],'source':r.get('source'),'status':rf['status']})
            for k,v in [('gross',gross),('refund',refund),('cost',cost),('logistics',shipping)]:sums[k]+=v
            details.append({'style':product['style'],'order':oid,'parent':r['parent'],'source':r.get('source'),'quantity':r['quantity'],'gross':cash(gross),'refund':cash(refund),'net_income':cash(gross-refund),'cost':cash(cost),'logistics':cash(shipping),'returned_quantity':qty if ret else None,'cost_reversed_quantity':qty,'return_basis':'evidence' if ret else 'business_assumption' if assumed else 'not_confirmed'})
        net=sums['gross']-sums['refund'];fee=net*sum(number(v) for v in p['fees'].values());fee=Decimal(cash(fee))
        pre=net-sums['cost']-sums['logistics']-fee
        relevant=[r for r in ads.values() if r['product']==pid and p['start']<=r['day']<=p['end']]
        expected=(date.fromisoformat(p['end'])-date.fromisoformat(p['start'])).days+1
        complete=len({r['day'] for r in relevant})==expected
        spend=sum((number(r['spend']) for r in relevant),Decimal(0)) if relevant else None
        # A partial series is retained for diagnosis, never silently treated as a complete zero-filled series.
        if not complete:issues.append({'type':'ad_days_incomplete','style':product['style'],'present_days':len({r['day'] for r in relevant}),'expected_days':expected})
        plan_spend=spend;estimated_ad=p.get('ad_basis')=='gross_gmv_rate'
        if estimated_ad:spend=Decimal(cash(sums['gross']*number(p['ad_rate'])))
        minimum=p.get('minimum_profit');ready=not missing_refund and (complete or estimated_ad)
        results.append({'style':product['style'],'product_id':pid,'paid_suborders':count,'gross_income':cash(sums['gross']),'refund':cash(sums['refund']),
            'net_income':None if missing_refund else cash(net),'product_cost':cash(sums['cost']),'estimated_fees':None if missing_refund else cash(fee),'logistics':cash(sums['logistics']),
            'pre_ad_profit':None if missing_refund else cash(pre),'ad_spend':cash(spend) if spend is not None else None,
            'post_ad_profit':cash(pre-spend) if ready else None,'allowed_ad_spend':cash(pre-number(minimum)) if minimum is not None and not missing_refund else None,
            'pending_return_cost':cash(sums['pending_return_cost']),'status':'ESTIMATE' if ready else 'INCOMPLETE','ad_days_complete':complete,
            'ad_basis':p.get('ad_basis','plan_spend'),'reference_plan_spend':cash(plan_spend) if plan_spend is not None else None,
            **(recommend_spend(sums['gross'],pre,number(p['ad_rate']),number(p['profit_target_rate'])) if estimated_ad and ready else {}),
            **(compare_ad_costs(sums['gross'],None if missing_refund else pre,plan_spend if complete else None,number(p['ad_rate'])) if estimated_ad else {})})
    for oid,r in orders.items():
        if r['product'] not in p['products']:issues.append({'type':'other_product_excluded','order':oid,'source':r.get('source')})
    return {'model_version':VERSION,'profile':p,'mode':'SHADOW','execution_allowed':False,'budget_actions':[],
        'finance_reconciliation':{'status':'PENDING','difference':None,'reason':'no_account_finance_statement'},
        'source_imports':[{'sha256':h,'as_of':t} for h,t in store.db.execute('SELECT hash,as_of FROM imports ORDER BY as_of,hash')],
        'products':results,'order_details':details,'exceptions':issues,
        'notification':{'status':'DRAFT_ONLY','sent':False,'group_id':None,'trader_id':None,'boss_id':None},
        'limitations':['order_status_proxy_not_refund_ledger','fees_and_shipping_estimated','source_export_cutoff_unknown','cost_effective_date_unknown','packaging_warehousing_unprovided','not_mature_profit','not_attributed_ad_profit']}
