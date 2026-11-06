"""Yesterday-only account report pipeline. Qianchuan GETs; no budget/feedback writes."""
import hashlib,json,os,subprocess,time,urllib.request,urllib.parse
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from . import hourly,qc_auth,plan_profit
from .reliability import ServiceError

LIST='/open_api/v1.0/qianchuan/uni_promotion/list/'
REPORT='/open_api/v1.0/qianchuan/report/uni_promotion/data/get/'
DETAIL='/open_api/v1.0/qianchuan/uni_promotion/ad/detail/'
GMV='total_pay_order_gmv_include_coupon_for_roi2'
TOPICS=[('OVERALL_ROI_PRODUCT_PROJECT','stat_cost_for_roi2','OVERALL_PROJECT'),
        ('SITE_PROMOTION_PRODUCT_AD','stat_cost','UNI_PROJECT'),
        ('OVERALL_ROI_LIVE_AWEME','stat_cost','LIVE')]

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));os.replace(temp,path)

def get(env,path,params):
    if path not in (LIST,REPORT,DETAIL):raise ValueError('Read-only endpoint required')
    def request(token):
        query=urllib.parse.urlencode({k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in params.items()})
        req=urllib.request.Request('https://api.oceanengine.com'+path+'?'+query,headers={'Access-Token':token},method='GET')
        with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
    for attempt in range(3):
        try:
            result=qc_auth.call(env,request)
        except ServiceError as exc:
            if getattr(exc,'api_code',None) not in (40100,429) or attempt==2:raise
            time.sleep(6*(attempt+1))
            continue
        if result.get('code')==0:return result
        if result.get('code') not in (40100,429):break
        time.sleep(2**attempt)
    raise ValueError('Qianchuan read failed code='+str(result.get('code')))

def pages(env,path,params,evidence,tag,request=get):
    rows=[];expected=None;ids=[]
    for page in range(1,1001):
        value=request(env,path,dict(params,page=page,page_size=100))
        save(evidence/(tag+'-'+str(page)+'.json'),{'request':dict(params,page=page,page_size=100),'response':value})
        if value.get('code')!=0:raise ValueError('API failed')
        data=value['data'];pi=data['page_info'];batch=data['ad_list' if path==LIST else 'rows']
        count=int(pi['total_num' if path==LIST else 'total_number']);total=int(pi['total_page'])
        if int(pi['page'])!=page or count<0 or total<0 or not isinstance(batch,list):raise ValueError('Invalid pagination')
        if expected is None:expected=(count,total)
        if expected!=(count,total) or (count>0 and (not batch or total<1)):raise ValueError('Unstable pagination')
        rows.extend(batch);ids.append(value.get('request_id') or value.get('log_id'))
        if page>=total:
            if len(rows)!=count:raise ValueError('Incomplete pagination')
            return rows,ids
    raise ValueError('Page limit')

def collect(env,day,today,evidence,request=get):
    if day!=(today-timedelta(days=1)).isoformat():raise ValueError('Only yesterday is allowed')
    account=hourly.account_id(env);common={'advertiser_id':account,'start_time':day+' 00:00:00','end_time':day+' 23:59:59'}
    inventory={};zeros=[];daily=[];checks=[];warnings=[]
    for goal in ['VIDEO_PROM_GOODS','LIVE_PROM_GOODS']:
        for scene in ['OVERALL_PROJECT','UNI_PROJECT']:
            args=dict(common,marketing_goal=goal,adlab_scene=scene,filtering={'status':'ALL'})
            rows,_=pages(env,LIST,args,evidence,goal+'-'+scene,request)
            scope=set()
            for ad in rows:
                info=ad['ad_info'];pid=str(info['id'])
                if pid in inventory:raise ValueError('Duplicate inventory plan')
                products=ad.get('product_info') or []
                name=products[0].get('product_name') if len(products)==1 else info.get('name')
                inventory[pid]={'plan_id':pid,'name':name or info.get('name') or '未命名计划','plan_name':info.get('name'),'scene':scene,'goal':goal,'platform_status':info.get('status')};scope.add(pid)
            if goal=='VIDEO_PROM_GOODS':
                try:
                    no,rids=pages(env,LIST,dict(args,filtering={'status':'ALL','having_cost':'NO'}),evidence,goal+'-'+scene+'-no-cost',request)
                    for row in no:
                        pid=str(row['ad_info']['id'])
                        if pid not in scope:raise ValueError('Inventory changed during acquisition')
                        zeros.append({'plan_id':pid,'date':day,'goal':goal,'request_id':','.join(filter(None,rids))})
                except Exception as exc:warnings.append(scene+'无消耗核验失败：'+type(exc).__name__)
    for topic,cost,scene in TOPICS:
        dims=['anchor_show_id','stat_time_day'] if scene=='LIVE' else ['ad_id','ad_name','stat_time_day'] if scene=='OVERALL_PROJECT' else ['ad_id','stat_time_day']
        params=dict(common,data_topic=topic,dimensions=dims,metrics=[cost,GMV],filters=[],order_by=[])
        try:rows,rids=pages(env,REPORT,params,evidence,topic,request)
        except Exception as exc:
            warnings.append(topic+'日报读取失败：'+type(exc).__name__);continue
        for row in rows:
            if row['dimensions']['stat_time_day']['ValueStr']!=day:raise ValueError('Report date mismatch')
        if scene=='LIVE':
            if rows:warnings.append('直播返回主播级数据，未分摊到单计划；详见原始响应。')
            else:warnings.append('直播日报无记录，不能确认昨日零消耗。')
        else:
            for row in rows:
                pid=str(row['dimensions']['ad_id']['Value'])
                name=inventory.get(pid,{}).get('name') or row['dimensions'].get('ad_name',{}).get('Value')
                if not name:
                    try:
                        detail=request(env,DETAIL,{'advertiser_id':account,'ad_id':int(pid)})
                        save(evidence/('detail-'+pid+'.json'),detail)
                        if detail.get('code')==0:name=detail['data'].get('name')
                    except Exception:warnings.append('个别报表补入计划名称待核验')
                daily.append({'plan_id':pid,'name':name or '报表补入计划 '+pid,'date':day,
                              'gmv':row['metrics'].get(GMV,{}).get('Value'),'spend':row['metrics'].get(cost,{}).get('Value'),
                              'source':{'topic':topic,'request_ids':rids}})
        try:
            agg,_=pages(env,REPORT,dict(params,dimensions=[]),evidence,topic+'-aggregate',request)
            if len(agg)>1:raise ValueError('Unexpected aggregate grain')
            for metric in [cost,GMV]:
                control=agg[0]['metrics'].get(metric,{}).get('Value') if agg else None
                detail=[r['metrics'].get(metric,{}).get('Value') for r in rows]
                delta=plan_profit.reconcile(detail,control)
                if delta is not None and Decimal(delta)!=0:raise ValueError('Report totals mismatch')
                checks.append({'topic':topic,'metric':metric,'aggregate':control,'difference':delta})
        except ValueError as exc:
            if str(exc) in ('Report totals mismatch','Unexpected aggregate grain'):raise
            warnings.append(topic+'汇总待核验：'+type(exc).__name__)
        except Exception as exc:warnings.append(topic+'汇总待核验：'+type(exc).__name__)
    if not inventory and not daily:raise ValueError('No inventory or daily evidence')
    return {'inventory':list(inventory.values()),'daily':daily,'no_spend':zeros,'date':day,'checks':checks,'warnings':warnings,'account':account}

def prepare(root,env,now,cfg,opts):
    root=Path(root);day=(now.date()-timedelta(days=1)).isoformat();account=hourly.account_id(env)
    config=json.loads((root/'config/profit_daily.json').read_text())
    policy_path=root/'data/trading_context'/account/'01_rules/rules.json'
    local=json.loads(policy_path.read_text());season=local['seasonal_refund_policy']
    if season['status']!='CONFIRMED' or str(season['advertiser_id'])!=account:raise ValueError('Unconfirmed refund policy')
    policy={'refund_rate':season['rates'][season['active_season']],'purchase_multiplier':local['purchase_multiplier'],
            'fee_rate':config['fee_rate'],'ad_rate':config['ad_rate'],'shipping_mode':'excluded'}
    out=root/'out/profit_daily'/account/day/now.strftime('%H%M%S%f');out.mkdir(parents=True)
    inputs=collect(env,day,now.date(),out/'source')
    result=plan_profit.report(inputs['inventory'],inputs['daily'],inputs['no_spend'],policy,date=day)
    result.update(account=account,policy=policy,checks=inputs['checks'],warnings=inputs['warnings'],as_of=now.isoformat(),policy_version=season['policy_version'])
    save(out/'result.json',result)
    subprocess.run([config['node'],str(root/'tools/profit_assets.mjs'),str(out/'result.json')],check=True,timeout=180,capture_output=True)
    subprocess.run([config['render_python'],str(root/'tools/profit_chart.py'),str(out/'result.json'),config['font']],check=True,timeout=90,capture_output=True)
    files=[{'kind':'image','path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(out.glob('profit-*.png'))]
    book=out/('profit-'+day+'.xlsx');files.append({'kind':'file','path':str(book),'sha256':hashlib.sha256(book.read_bytes()).hexdigest()})
    if not files or len(files)==1:raise ValueError('Chart missing')
    actors=cfg['notify']['roles'].get('投手',[])
    if not actors or any(not str(a).startswith('ou_') for a in actors):raise ValueError('Operator mention configuration required')
    totals=result['totals_calculable_only'];zero=sum(r['profit'] is None and r['spend']=='0.00' for r in result['rows'])
    show=lambda key:totals[key] if totals[key] is not None else '待核验'
    text=(f'【昨日计划利润测算｜{day}】\n共{len(result["rows"])}个计划，{result["calculable"]}个可测算；{zero}个已核实无消耗，未返回的GMV及利润仍待核验。\n'
          f'可测算部分：GMV {show("gmv")}元，已花广告费{show("spend")}元，预估利润{show("profit")}元。\n'
          '历史退款情景估算，未计运费与固定费用。今天可执行追加额度待核验，不是今天可直接追加的预算。\n'
          '无需反馈或与机器人交互，未调整预算。')
    if result['warnings']:text+='\n数据核查：'+'；'.join(result['warnings'])
    return {'type':'profit_bundle','date':day,'account':account,'directory':str(out),'files':files,'text':text,'actors':actors}
