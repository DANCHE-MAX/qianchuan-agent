"""Attach two complete-day comparison images to a new scheduled profit bundle only."""
import hashlib,json,subprocess
from pathlib import Path
from decimal import Decimal
from datetime import timedelta
from . import product_spend_daily as spend,profit_daily as p,plan_profit,hourly


def grouped_plans(day):
    plans={}
    for r in day['rows']:
        entry=plans.setdefault(r['plan'],{'product':r['product'],'name':r['name'],'managed':r.get('managed',False),'spend':Decimal(0),'gmv':Decimal(0)})
        if (entry['product'],entry['name'])!=(r['product'],r['name']):raise ValueError('Conflicting product mapping')
        entry['spend']+=spend.amount(r['amount']);entry['gmv']+=spend.amount(r['gmv'])
    return plans


def reconcile_main(day,main):
    """Don't present companion figures contradicting the original daily attachment."""
    if not day['complete']:return
    plans=grouped_plans(day);base={str(r['plan_id']):r for r in main['rows']}
    mismatch=False
    for pid,row in plans.items():
        b=base.get(pid,{})
        if any(b.get(k) is None or plan_profit.money(spend.amount(b[k]))!=plan_profit.money(row[k]) for k in ('gmv','spend')):mismatch=True
    for pid,row in base.items():
        if pid not in plans and any(row.get(k) is not None and spend.amount(row[k])!=0 for k in ('gmv','spend')):mismatch=True
    if mismatch:
        day['complete']=False;day['warnings'].append('与原日报采集结果不一致，待核验')


def pie_view(days,policy):
    result=[];names={};ranking={}
    for day in days:
        missing=None;groups={}
        if day['complete']:
            plans=grouped_plans(day)
            if day.get('inventory_complete'):missing=len(set(day.get('inventory',[]))-set(plans))
            for plan_id,plan in plans.items():
                pid='managed' if plan.get('managed') else plan['product'] or 'unknown';name='全店托管' if pid=='managed' else plan['name'] if plan['product'] else '商品归属待核验'
                g=groups.setdefault(pid,{'id':pid,'name':name,'spend':Decimal(0),'profit':Decimal(0)})
                g['spend']+=plan['spend'];g['profit']+=Decimal(plan_profit.calculate(plan['gmv'],plan['spend'],plan_profit.resolve_policy(day.get('profit_policy',policy),plan_id))['profit'])
            for pid,g in groups.items():names[pid]=g['name'];ranking[pid]=ranking.get(pid,Decimal(0))+g['spend']
        if 'report_missing' in day:missing=day['report_missing']
        positive=[g for g in groups.values() if g['id'] not in ('unknown','managed') and g['profit']>0]
        loss=sorted([g for g in groups.values() if g['id'] not in ('unknown','managed') and g['profit']<0],key=lambda g:g['profit'])
        ads=[g for g in groups.values() if g['spend']>0]
        def shares(items,key):
            total=sum((g[key] for g in items),Decimal(0))
            return [dict(id=g['id'],name=g['name'],amount=str(g[key]),share=str(g[key]/total*100)) for g in sorted(items,key=lambda g:-g[key])]
        result.append({'day':day['day'],'complete':day['complete'],'positive':shares(positive,'profit'),'ads':shares(ads,'spend'),
            'positive_total':str(sum((g['profit'] for g in positive),Decimal(0))) if day['complete'] else None,
            'ad_total':str(sum((g['spend'] for g in ads),Decimal(0))) if day['complete'] else None,
            'loss_total':str(sum((g['profit'] for g in loss),Decimal(0))) if day['complete'] else None,
            'losses':[{'id':g['id'],'name':g['name'],'amount':str(g['profit'])} for g in loss],
            'unknown_profit':str(groups['unknown']['profit']) if 'unknown' in groups else None,
            'managed_profit':str(groups['managed']['profit']) if 'managed' in groups else None,
            'managed_present':'managed' in groups,'unknown_present':'unknown' in groups,'missing_plans':missing,'warnings':day.get('warnings',[])})
    colors=['#1769E0','#F2A000','#00A67D','#8357C5','#D85B31','#5D8B38','#AC7224','#36A5AE','#D3639F','#67717A']
    ids=sorted([x for x in ranking if x not in ('unknown','managed')],key=lambda x:(-ranking[x],x))
    legend=[{'id':pid,'name':names[pid],'color':colors[i%len(colors)]} for i,pid in enumerate(ids)]
    legend.append({'id':'unknown','name':'商品归属待核验','color':'#64748B'})
    legend.append({'id':'managed','name':'全店托管','color':'#C43D87'})
    return {'days':result,'legend':legend}


def attach(root,env,now,bundle):
    root=Path(root)
    if now.tzinfo is None:raise ValueError('Aware daily schedule required')
    now=now.astimezone(hourly.CHINA);days=[(now.date()-timedelta(days=i)).isoformat() for i in (2,1)]
    if bundle['date']!=days[1] or str(bundle['account'])!=hourly.account_id(env):raise ValueError('Daily comparison scope mismatch')
    directory=Path(bundle['directory']);main=json.loads((directory/'result.json').read_text())
    if main['date']!=days[1] or str(main['account'])!=hourly.account_id(env):raise ValueError('Main report scope mismatch')
    folder=directory/'two_day';folder.mkdir(parents=True,exist_ok=True)
    inputs=spend.collect(env,days,folder/'source',include_gmv=True)
    if [d['day'] for d in inputs]!=days:raise ValueError('Comparison dates mismatch')
    reconcile_main(inputs[-1],main)
    from . import daily_guide
    reports=[]
    for item in inputs:
        item['account']=str(main['account'])
        report=main if item['day']==main['date'] else daily_guide.previous(root,str(main['account']),item['day'])
        reports.append(report)
        if report:
            if report.get('policy')!=main['policy']:
                item['complete']=False;item['policy_transition']=True;item['warnings'].append('新旧模型版本切换，利润比较待核验')
            reconcile_main(item,report)
            item['report_missing']=report['missing_profit']
            if report['policy'].get('model_version'):item['profit_policy']=report['policy']
            by_id={str(r['plan_id']):r for r in report['rows']}
            for row in item['rows']:
                info=by_id.get(row['plan'],{})
                row['managed']='全店托管' in str(info.get('plan_name') or info.get('name',''))
    from . import product_identity
    for item,report in zip(inputs,reports):
        if report:
            display,_=product_identity.overlay(root,report,None)
            product_identity.merge_rows(item,display)
    from . import refund_differences
    confirmations=refund_differences.records(root)
    for item,report in zip(inputs,reports):
        if report:refund_differences.annotate(item,report,confirmations)
    p.save(folder/'collected.json',inputs)
    p.save(folder/'reports.json',reports)
    stack=spend.build(inputs);pies=pie_view(inputs,main['policy'])
    p.save(folder/'stack.json',stack);p.save(folder/'pies.json',pies)
    config=json.loads((root/'config/profit_daily.json').read_text())
    files=[]
    for name,tool in [('stack','product_spend_daily_image.py'),('pies','two_day_pies_image.py')]:
        target=folder/(name+'.png')
        subprocess.run([config['render_python'],str(root/'tools'/tool),str(folder/(name+'.json')),str(target),config['font']],check=True,capture_output=True,timeout=90)
        files.append({'kind':'image','path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    result=dict(bundle);result['files']=[*bundle['files'],*files]
    result['text']+='\n附两日消耗对比与四宫格：'+days[0]+'、'+days[1]+'（北京时间完整自然日）。盈利贡献只含已关联且可测算的盈利商品；亏损、全店托管利润和缺失项单列。'
    result['comparison_days']=days
    return result
