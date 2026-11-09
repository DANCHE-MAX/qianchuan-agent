"""Evidence-bound local product labels; never mutates frozen reports or ad state."""
from pathlib import Path
from copy import deepcopy
import hashlib,json


def verified(path,expected):
    try:return bool(expected) and hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected
    except (OSError,TypeError):return False


def records(root,account):
    path=Path(root)/'data/product_identity/confirmed.json'
    if not path.exists():return []
    try:items=json.loads(path.read_text())
    except (ValueError,OSError):return []
    if not isinstance(items,list):return []
    selected=[r for r in items if str(r.get('account'))==str(account) and r.get('name') and r.get('product_id') and verified(r.get('name_evidence'),r.get('name_sha256'))]
    # Ambiguity must not silently pick a record.
    return [r for r in selected if sum(str(x.get('plan_id'))==str(r.get('plan_id')) for x in selected)==1]


def match(record,detail):
    products=detail.get('data',{}).get('product_infos') or []
    return detail.get('code')==0 and len(products)==1 and str(products[0].get('product_id'))==str(record['product_id'])


def inventory_record(record,doc,day):
    args=doc.get('request',{});response=doc.get('response',{})
    if str(args.get('advertiser_id'))!=str(record['account']) or args.get('start_time')!=day+' 00:00:00' or args.get('end_time')!=day+' 23:59:59' or response.get('code')!=0:return None
    ads=[a for a in response.get('data',{}).get('ad_list',[]) if str(a.get('ad_info',{}).get('id'))==str(record['plan_id'])]
    if len(ads)!=1:return None
    products=ads[0].get('product_info') or []
    if len(products)!=1 or str(products[0].get('product_id'))!=str(record['product_id']) or not products[0].get('product_name'):return None
    return dict(record,name=products[0]['product_name'])


def label(record):
    return record['name']+'（历史商品名；'+(str(record['sku']) if record.get('sku') else '款号待核验')+'）'


def apply(row,record):
    row.setdefault('plan_name',row['name']);row['name']=label(record)
    row.update(product_id=str(record['product_id']),product_name=record['name'],sku=record.get('sku'),product_identity_source='local_verified_historical_title',sku_status='已核实' if record.get('sku') else '待核验')


def overlay(root,report,mapping):
    """In-memory display only, supported by exact historical-day binding evidence."""
    out=deepcopy(report);m=deepcopy(mapping) if mapping else {'day':report['date'],'account':str(report['account']),'rows':[]}
    changed=False
    for rec in records(root,report['account']):
        binding=rec.get('bindings',{}).get(report['date'],{})
        if not verified(binding.get('path'),binding.get('sha256')):continue
        try:detail=json.loads(Path(binding['path']).read_text())
        except (ValueError,OSError):continue
        if binding.get('kind')=='inventory':
            rec=inventory_record(rec,detail,report['date'])
            if rec is None:continue
        elif not match(rec,detail):continue
        pid=str(rec['plan_id']);rows=[r for r in out['rows'] if str(r['plan_id'])==pid]
        existing=[r for r in m['rows'] if str(r['plan'])==pid]
        if any(r.get('product') and str(r['product'])!=str(rec['product_id']) for r in existing):continue
        for row in rows:
            if '全店托管' in str(row.get('plan_name') or row['name']):continue
            apply(row,rec);changed=True
            if not existing:m['rows'].append({'plan':pid,'product':str(rec['product_id']),'name':label(rec)})
            else:
                for item in existing:item.update(product=str(rec['product_id']),name=label(rec))
    return (out,m) if changed else (deepcopy(report),deepcopy(mapping))


def capture(root,env,report,evidence,request=None):
    """Future new bundles only: re-read candidate binding, record proof before rendering."""
    from . import profit_daily as p
    request=request or p.get;out=deepcopy(report);proof=[];evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    for rec in records(root,report['account']):
        rows=[r for r in out['rows'] if str(r['plan_id'])==str(rec['plan_id'])]
        if not rows:continue
        # Reuse this acquisition's scoped inventory before any detail call.
        candidates=[]
        for path in evidence.glob('VIDEO_PROM_GOODS-*.json'):
            if 'no-cost' in path.name:continue
            try:found=inventory_record(rec,json.loads(path.read_text()),report['date'])
            except (ValueError,OSError):found=None
            if found:candidates.append(found)
        if len(candidates)==1:
            for row in rows:
                if '全店托管' not in str(row.get('plan_name') or row['name']):apply(row,candidates[0])
            proof.append(candidates[0]);continue
        if rec.get('binding_kind')=='inventory':
            out.setdefault('warnings',[]).append('本次列表未确认唯一商品绑定，不沿用历史映射，归属待核验');continue
        try:detail=request(env,p.DETAIL,{'advertiser_id':report['account'],'ad_id':int(rec['plan_id'])})
        except Exception:
            out.setdefault('warnings',[]).append('商品绑定复查失败，商品款号及归属待核验');continue
        p.save(evidence/('identity-detail-'+str(rec['plan_id'])+'.json'),detail)
        if not match(rec,detail):
            out.setdefault('warnings',[]).append('商品绑定已变化或不唯一，不使用历史商品名，归属待核验');continue
        for row in rows:
            if '全店托管' not in str(row.get('plan_name') or row['name']):apply(row,rec)
        proof.append(rec)
    p.save(evidence/'product-identities.json',proof)
    return out


def merge_rows(day,report):
    """Apply new-bundle identities consistently to the two-day chart mapping."""
    by_id={str(r['plan_id']):r for r in report['rows'] if r.get('product_identity_source')=='local_verified_historical_title'}
    for item in day['rows']:
        row=by_id.get(str(item['plan']))
        if not row or item.get('managed'):continue
        if item.get('product') and str(item['product'])!=row['product_id']:raise ValueError('Verified product identity conflicts with inventory')
        item.update(product=row['product_id'],name=row['name'])
