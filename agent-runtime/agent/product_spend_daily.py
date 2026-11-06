"""Midnight, read-only two-day product spend comparison with pinned durable delivery."""
import hashlib,json,subprocess
from pathlib import Path
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from . import profit_daily as p,hourly,settings
from .profit_delivery import deliver
from .reliability import file_lock
from .notify import feishu_allowed_chat_ids


def amount(value):
    n=Decimal(str(value))
    if not n.is_finite() or n<0:raise ValueError('Invalid spend')
    return n


def parse(rows,day,metric,mapping):
    seen=set();result=[]
    for row in rows:
        dims=row['dimensions'];pid=str(dims['ad_id']['Value']);at=dims['stat_time_hour']['ValueStr']
        if dims['stat_time_day']['ValueStr']!=day or not at.startswith(day+' '):raise ValueError('Wrong report date')
        hour=int(at[11:13]);key=(pid,hour)
        if not 0<=hour<24 or key in seen:raise ValueError('Duplicate/invalid hour')
        seen.add(key);product=mapping.get(pid)
        result.append({'slot':hour//2,'plan':pid,'product':product['id'] if product else None,
                       'name':product['name'] if product else '', 'amount':str(amount(row['metrics'][metric]['Value']))})
    return result


def collect(env,days,evidence,*,include_gmv=False):
    result=[]
    for day in days:
        common={'advertiser_id':hourly.account_id(env),'start_time':day+' 00:00:00','end_time':day+' 23:59:59'}
        allrows=[];warnings=[];conflicts=[];complete=True;seen_plans=set();inventory=set();inventory_complete=True
        for scene,topic,metric,hourdim in [('OVERALL_PROJECT','OVERALL_ROI_PRODUCT_PROJECT','stat_cost_for_roi2','range_stat_time_hour'),('UNI_PROJECT','SITE_PROMOTION_PRODUCT_AD','stat_cost','stat_time_hour')]:
            mapping={};managed=set()
            try:
                ads,_=p.pages(env,p.LIST,dict(common,marketing_goal='VIDEO_PROM_GOODS',adlab_scene=scene,filtering={'status':'ALL'}),evidence,day+'-'+scene+'-inventory')
                for ad in ads:
                    inventory.add(str(ad['ad_info']['id']))
                    if '全店托管' in str(ad['ad_info'].get('name','')):managed.add(str(ad['ad_info']['id']))
                    products=ad.get('product_info') or []
                    if len(products)==1 and products[0].get('product_id') and products[0].get('product_name'):
                        mapping[str(ad['ad_info']['id'])]={'id':str(products[0]['product_id']),'name':products[0]['product_name']}
            except Exception:
                inventory_complete=False;warnings.append(scene+'商品关联待核验')
            args=dict(common,data_topic=topic,metrics=[metric,p.GMV] if include_gmv else [metric],filters=[],order_by=[])
            try:
                rows,_=p.pages(env,p.REPORT,dict(args,dimensions=['ad_id','stat_time_day',hourdim]),evidence,day+'-'+scene+'-hours')
                totals,_=p.pages(env,p.REPORT,dict(args,dimensions=[]),evidence,day+'-'+scene+'-total')
                parsed=parse(rows,day,metric,mapping)
                for item in parsed:item["managed"]=item["plan"] in managed
                if len(totals)!=1:raise ValueError('Aggregate unavailable')
                if sum((amount(r['amount']) for r in parsed),Decimal(0))!=amount(totals[0]['metrics'][metric]['Value']):raise ValueError('Reconciliation mismatch')
                if include_gmv:
                    if sum((amount(r['metrics'][p.GMV]['Value']) for r in rows),Decimal(0))!=amount(totals[0]['metrics'][p.GMV]['Value']):raise ValueError('GMV reconciliation mismatch')
                    for raw,item in zip(rows,parsed):item['gmv']=str(amount(raw['metrics'][p.GMV]['Value']))
                ids={r['plan'] for r in parsed}
                if ids & seen_plans:raise ValueError('Overlapping report scopes')
                seen_plans.update(ids);allrows.extend(parsed)
            except Exception as exc:
                complete=False;warnings.append(scene+'小时明细或对账待核验')
                if isinstance(exc,ValueError) and str(exc) in ('Reconciliation mismatch','GMV reconciliation mismatch','Overlapping report scopes','Wrong report date','Duplicate/invalid hour','Invalid spend'):
                    conflicts.append(scene+':'+str(exc))
        # Live data is not allocated to products without a supported mapping.
        try:
            live,_=p.pages(env,p.REPORT,dict(common,data_topic='OVERALL_ROI_LIVE_AWEME',dimensions=[],metrics=['stat_cost'],filters=[],order_by=[]),evidence,day+'-live-total')
            if len(live)!=1 or amount(live[0]['metrics']['stat_cost']['Value'])!=0:warnings.append('直播消耗未纳入，覆盖待核验')
        except Exception:warnings.append('直播消耗覆盖待核验')
        result.append({'day':day,'rows':allrows,'complete':complete,'warnings':warnings,'inventory':sorted(inventory),'inventory_complete':inventory_complete,'conflicts':conflicts})
    return result


def build(days):
    totals=defaultdict(lambda:Decimal(0));names={}
    for day in days:
        if not day['complete']:continue
        for r in day['rows']:
            if r['product'] is not None and not r.get('managed'):totals[r['product']]+=amount(r['amount']);names[r['product']]=r['name']
    top=sorted(totals,key=lambda k:(-totals[k],k))[:3];series=[];unknown=Decimal(0);managed_total=Decimal(0)
    for day in days:
        slots=[]
        for i in range(12):
            if not day['complete']:slots.append(None);continue
            bins=[Decimal(0) for _ in range(6)]
            for r in day['rows']:
                if r['slot']!=i:continue
                idx=5 if r.get('managed') else top.index(r['product']) if r['product'] in top else 4 if r['product'] is None else 3
                bins[idx]+=amount(r['amount'])
            unknown+=bins[4];managed_total+=bins[5];slots.append(list(map(str,bins)))
        series.append({'day':day['day'],'slots':slots,'total':str(sum((sum(map(Decimal,b)) for b in slots),Decimal(0))) if day['complete'] else None})
    return {'products':[{'name':names[k],'total':str(totals[k])} for k in top], 'series':series,
            'managed':str(managed_total) if any(d['complete'] for d in days) else None,
            'unknown':str(unknown) if any(d['complete'] for d in days) else None,'complete':all(d['complete'] for d in days),
            'warnings':sorted({w for d in days for w in d.get('warnings',[])})}


def prepare(root,env,days,directory,actors):
    data=collect(env,days,directory/'source');p.save(directory/'collected.json',data)
    view=build(data);p.save(directory/'chart-data.json',view)
    cfg=settings.load_config(root/'config/profit_daily.json');path=directory/'comparison.png'
    subprocess.run([cfg['render_python'],str(root/'tools/product_spend_daily_image.py'),str(directory/'chart-data.json'),str(path),cfg['font']],check=True,capture_output=True,timeout=60)
    return {'directory':str(directory),'date':days[1],'actors':actors,
            'text':days[1]+' 对比 '+days[0]+'｜两天商品广告消耗。前三商品固定配色；全店托管含未拆分多商品计划，缺失标待核验。仅供复盘，未调整投放。',
            'files':[{'kind':'image','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}


def tick(engine,now=None):
    """Retired: both comparison images now travel with the scheduled daily bundle."""
    return False
