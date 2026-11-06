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


def collect(env,days,evidence):
    result=[]
    for day in days:
        common={'advertiser_id':hourly.account_id(env),'start_time':day+' 00:00:00','end_time':day+' 23:59:59'}
        allrows=[];warnings=[];complete=True;seen_plans=set()
        for scene,topic,metric,hourdim in [('OVERALL_PROJECT','OVERALL_ROI_PRODUCT_PROJECT','stat_cost_for_roi2','range_stat_time_hour'),('UNI_PROJECT','SITE_PROMOTION_PRODUCT_AD','stat_cost','stat_time_hour')]:
            mapping={}
            try:
                ads,_=p.pages(env,p.LIST,dict(common,marketing_goal='VIDEO_PROM_GOODS',adlab_scene=scene,filtering={'status':'ALL'}),evidence,day+'-'+scene+'-inventory')
                for ad in ads:
                    products=ad.get('product_info') or []
                    if len(products)==1 and products[0].get('product_id') and products[0].get('product_name'):
                        mapping[str(ad['ad_info']['id'])]={'id':str(products[0]['product_id']),'name':products[0]['product_name']}
            except Exception:warnings.append(scene+'商品关联待核验')
            args=dict(common,data_topic=topic,metrics=[metric],filters=[],order_by=[])
            try:
                rows,_=p.pages(env,p.REPORT,dict(args,dimensions=['ad_id','stat_time_day',hourdim]),evidence,day+'-'+scene+'-hours')
                totals,_=p.pages(env,p.REPORT,dict(args,dimensions=[]),evidence,day+'-'+scene+'-total')
                parsed=parse(rows,day,metric,mapping)
                if len(totals)!=1:raise ValueError('Aggregate unavailable')
                if sum((amount(r['amount']) for r in parsed),Decimal(0))!=amount(totals[0]['metrics'][metric]['Value']):raise ValueError('Reconciliation mismatch')
                ids={r['plan'] for r in parsed}
                if ids & seen_plans:raise ValueError('Overlapping report scopes')
                seen_plans.update(ids);allrows.extend(parsed)
            except Exception:complete=False;warnings.append(scene+'小时明细或对账待核验')
        # Live data is not allocated to products without a supported mapping.
        try:
            live,_=p.pages(env,p.REPORT,dict(common,data_topic='OVERALL_ROI_LIVE_AWEME',dimensions=[],metrics=['stat_cost'],filters=[],order_by=[]),evidence,day+'-live-total')
            if len(live)!=1 or amount(live[0]['metrics']['stat_cost']['Value'])!=0:warnings.append('直播消耗未纳入，覆盖待核验')
        except Exception:warnings.append('直播消耗覆盖待核验')
        result.append({'day':day,'rows':allrows,'complete':complete,'warnings':warnings})
    return result


def build(days):
    totals=defaultdict(lambda:Decimal(0));names={}
    for day in days:
        if not day['complete']:continue
        for r in day['rows']:
            if r['product'] is not None:totals[r['product']]+=amount(r['amount']);names[r['product']]=r['name']
    top=sorted(totals,key=lambda k:(-totals[k],k))[:3];series=[];unknown=Decimal(0)
    for day in days:
        slots=[]
        for i in range(12):
            if not day['complete']:slots.append(None);continue
            bins=[Decimal(0) for _ in range(5)]
            for r in day['rows']:
                if r['slot']!=i:continue
                idx=top.index(r['product']) if r['product'] in top else 4 if r['product'] is None else 3
                bins[idx]+=amount(r['amount'])
            unknown+=bins[4];slots.append(list(map(str,bins)))
        series.append({'day':day['day'],'slots':slots,'total':str(sum((sum(map(Decimal,b)) for b in slots),Decimal(0))) if day['complete'] else None})
    return {'products':[{'name':names[k],'total':str(totals[k])} for k in top], 'series':series,
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
    now=now or hourly.now_china()
    if now.tzinfo is None:raise ValueError('Aware time required')
    now=now.astimezone(hourly.CHINA);root=Path(engine.root)
    # Only this midnight window, never replay an old day's report on daytime startup.
    if now.hour!=0 or not (root/'config/profit_daily.json').exists():return False
    cfg=settings.load_config(root/'config/rules.json')
    if not cfg.get('monitoring',{}).get('enabled'):return False
    account=hourly.account_id(engine.env);midnight=now.replace(minute=0,second=0,microsecond=0)
    samples=engine.store.balances(account,midnight-timedelta(minutes=5))
    from .balance_cycle import stamp
    if not any(str(s.get('advertiser_id'))==account and midnight<=stamp(s['fetched_at'])<=midnight+timedelta(minutes=5) for s in samples):return False
    chat=engine.env.get('FEISHU_ALERT_CHAT_ID') or engine.env.get('FEISHU_CHAT_ID')
    allowed=feishu_allowed_chat_ids(engine.env) or [engine.env.get('FEISHU_CHAT_ID')]
    if not chat or chat not in allowed:raise ValueError('Comparison chat not allowed')
    actors=cfg.get('notify',{}).get('roles',{}).get('投手',[])
    if not isinstance(actors,list) or not actors or not all(isinstance(x,str) and x.startswith('ou_') for x in actors):raise ValueError('Configured operator required')
    days=[(now.date()-timedelta(days=i)).isoformat() for i in (2,1)]
    directory=root/'out/product_spend_daily'/account/days[1];directory.mkdir(parents=True,exist_ok=True)
    statefile=directory/'state.json'
    with file_lock(directory/'job.lock'):
        state=json.loads(statefile.read_text()) if statefile.exists() else {'account':account,'chat':chat,'days':days}
        if (state['account'],state['chat'],state['days'])!=(account,chat,days):raise ValueError('Pinned comparison scope changed')
        if state.get('sent'):return False
        if not statefile.exists():p.save(statefile,state)
        if 'bundle' not in state:
            state['bundle']=prepare(root,engine.env,days,directory,actors);p.save(statefile,state)
        deliver(state['bundle'],engine.notifier_factory(chat),hashlib.sha256((account+':'+days[1]+':product-spend').encode()).hexdigest())
        state['sent']=True;p.save(statefile,state);return True
