"""Evidence-first context checks and opt-in read-API maintenance; never sends messages."""
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import uuid

from . import analysis_gate as gate, context_sources as sources, context_finance as finance
from . import trading_context as context

VERSION=29


def clock(now):
    now=now or datetime.now(context.CHINA)
    if now.tzinfo is None:raise ValueError('检查时间必须带时区')
    return now.astimezone(context.CHINA)


def snapshot_series(root,account,kind,now,gaps):
    tomorrow=(now.date()+timedelta(days=1)).isoformat()
    return sources.query(root,'data/qianchuan/monitor.db',
        'SELECT payload FROM snapshots WHERE account=? AND kind=? AND bucket>=? AND bucket<? ORDER BY bucket',
        (account,kind,now.date().isoformat()+'T00:00:00+08:00',tomorrow+'T00:00:00+08:00'),gaps)


def snapshot_diagnostic(root,account,kind,now,gaps):
    raw_series=snapshot_series(root,account,kind,now,gaps);series=[];future_points=[]
    for item in raw_series:
        try:
            if any(sources.instant(item[k])>now for k in ('request_end','fetched_at')):
                future_points.append({k:item.get(k) for k in ('bucket','request_end','fetched_at')});continue
        except (KeyError,TypeError,ValueError):pass
        series.append(item)
    admitted=gate.assess(series,account,kind,now,stale_minutes=gate.stale_minutes(root))
    points=[];pairs=[]
    for item in series[-6:]:
        try:
            parsed,error=gate.validate(item,account,kind,now)
            point={k:item.get(k) for k in ('bucket','request_end','fetched_at')}
            point['metadata_valid']=error is None
            point['future']=any(sources.instant(item[k])>now for k in ('request_end','fetched_at'))
            point['capture_lag_seconds']=str(Decimal(str((sources.instant(item['fetched_at'])-sources.instant(item['request_end'])).total_seconds())))
            points.append(point)
        except (KeyError,ValueError,TypeError,AttributeError):
            points.append({'metadata_valid':False,'reason':'invalid_timestamp_or_record'})
    for index,(a,b) in enumerate(zip(points,points[1:])):
        try:
            seconds=(sources.instant(b['request_end'])-sources.instant(a['request_end'])).total_seconds()
            bucket_step=(sources.instant(b['bucket'])-sources.instant(a['bucket'])).total_seconds()
            pairs.append({'from':a['request_end'],'to':b['request_end'],
                          'elapsed_minutes':str(seconds/60),'bucket_step_minutes':str(bucket_step/60),
                          'bucket_consecutive':bucket_step==1800,'interval_25_to_40_minutes':1500<=seconds<=2400,
                          'in_latest_three':index>=len(points)-3})
        except (KeyError,ValueError,TypeError):pairs.append({'reason':'invalid_pair'})
    if 'gap' in admitted['reasons']:
        suggestion='最近3点中存在缺桶或实际截止间隔不在25—40分钟内。按recent_pairs确认具体一对；不能据此推断调度根因。保留原时间，继续观察后续真实采集。'
    elif admitted['state'] in ('ready','partial'):
        suggestion='最近3点时间条件通过；partial仍须按对象/指标资格使用。全日旧缺口不因此自动补齐。'
    else:suggestion='依据admission.reasons核对样本、时效或来源。不要补造累计快照或放宽准入。'
    return {'admission':gate.public(admitted),'today_points':len(raw_series),'recent_points':points,
            'excluded_future_points':future_points[-6:],
            'recent_pairs':pairs,'suggestion':suggestion}


def history_diagnostic(root,account,now,rules,days,gaps):
    accepted=sources.history(root,account,now,rules,days,gaps)
    accepted_paths={x['source'] for x in accepted}
    home=context.account_home(root,account)
    folders=(sources.inside(root,Path(root)/'out'),sources.inside(root,home/'02_data/history'))
    counts=Counter();excluded=[]
    from .qianchuan import TOPIC
    for folder in folders:
        if not folder.exists():continue
        for path in sorted(folder.glob('model_*.json')):
            counts['scanned']+=1
            if path.name.endswith('_cards.json'):counts['cards_excluded']+=1;continue
            try:
                sources.inside(root,path);value,_=sources.load_json(path)
                meta=value.get('source_api') or {}
                if str(meta.get('advertiser_id'))!=account:counts['other_account']+=1;continue
                relative=path.relative_to(root).as_posix()
                if relative in accepted_paths:counts['accepted']+=1;continue
                reason='invalid_or_inconsistent_report'
                if meta.get('data_topic')!=TOPIC or meta.get('monetary_unit')!='CNY':reason='topic_or_unit_mismatch'
                else:
                    end=datetime.fromisoformat(value['audit']['end']).date()
                    if end>=now.date():reason='current_or_future_window'
                    elif end<now.date()-timedelta(days=days):reason='outside_history_window'
                    elif sources.instant(meta['fetched_at'])>now:reason='future_source_time'
                counts[reason]+=1;excluded.append({'source':relative,'reason':reason})
            except (ValueError,KeyError,TypeError,AttributeError,OSError):counts['unreadable_or_not_report']+=1
    return {'out_directory_exists':folders[0].exists(),'cache_directory_exists':folders[1].exists(),
            'counts':dict(counts),'accepted_windows':[{'source':x['source'],'window':x['window']} for x in accepted],
            'excluded':excluded,'limitation':'其他账户只统计文件数，不读取其经营内容；没有文件和被拒绝是不同原因。'}


def inspect(root,account,*,now=None,history_days=30):
    root=Path(root).resolve();now=clock(now);home=context.initialize(root,account);gaps=[]
    if not 1<=history_days<=366:raise ValueError('历史范围须为1至366天')
    rules,_=sources.load_json(sources.inside(root,home/'01_rules/rules.json'));finance.validate_rules(rules,account)
    from . import settings
    enabled=None;cfg={}
    path=sources.inside(root,root/'config/rules.json')
    if path.exists():
        try:cfg,_=sources.load_json(path);enabled=cfg.get('monitoring',{}).get('enabled')
        except (ValueError,OSError,AttributeError):gaps.append('monitoring_config_unreadable')
    signals=sources.query(root,'data/qianchuan/health.db',
        "SELECT json_object('name',name,'stamp',stamp,'state',state) FROM signals WHERE account=?",(account,),gaps)
    return {'version':VERSION,'advertiser_id':account,'checked_at':now.isoformat(),
            'monitoring_enabled':enabled,'task_signals':signals,
            'snapshots':{kind:snapshot_diagnostic(root,account,kind,now,gaps) for kind in settings.collection_kinds(cfg,account)},
            'history':history_diagnostic(root,account,now,rules,history_days,gaps),'read_gaps':sorted(set(gaps)),
            'limitation':'进程心跳不证明飞书连通。检查不修改观察点，不证明云端漏采原因已经找到。'}


def validate_account(root,account,env):
    context.account_home(root,account)
    if env.get('QC_ADVERTISER_IDS','').strip()!=account or not env.get('QC_ACCESS_TOKEN','').strip():
        raise ValueError('API配置账户与目标账户不一致，或缺少访问令牌；未发起读取')


def collect_yesterday(root,account,env,*,now=None):
    from . import qianchuan, model
    now=clock(now);root=Path(root).resolve();validate_account(root,account,env)
    home=context.initialize(root,account);rules,_=sources.load_json(sources.inside(root,home/'01_rules/rules.json'))
    finance.validate_rules(rules,account)
    day=(now.date()-timedelta(days=1)).isoformat();gaps=[]
    existing=[x for x in sources.history(root,account,now,rules,30,gaps)
              if x['window']=={'start':day,'end':day,'days':1}]
    if existing:return {'created':False,'date':day,'source':existing[-1]['source'],'reason':'同账户昨日单日事实已可读取，未重复请求'}
    rows,meta=qianchuan.fetch_daily(env,day,days=1,today=now.date());completed=clock(None)
    if (str(meta.get('advertiser_id'))!=account or meta.get('data_topic')!=qianchuan.TOPIC
            or meta.get('monetary_unit')!='CNY' or meta.get('start')!=day or meta.get('end')!=day or meta.get('days')!=1
            or sources.instant(meta['fetched_at']).date()<=datetime.fromisoformat(day).date()
            or not now-timedelta(minutes=1)<=sources.instant(meta['fetched_at'])<=completed):
        raise ValueError('日报来源账户、窗口、范围、金额单位或时间无效')
    selected,audit=model.select_rows(rows,day,1,today=now.date())
    if any(not p['plan_id'].isascii() or not p['plan_id'].isdigit() or not p['plan_name'].strip() for p in selected):
        raise ValueError('历史计划ID或名称无效')
    plans=[{k:(str(v) if isinstance(v,Decimal) else v) for k,v in row.items() if k!='date'} for row in selected]
    totals={k:str(sum((Decimal(row[k]) for row in plans),Decimal(0))) for k in model.FIELDS}
    # Raw read evidence is saved away from old model report paths; no legacy predicted profit is copied.
    raw_dir=sources.inside(root,root/'data/qianchuan/context_history'/account/(day+'_'+uuid.uuid4().hex[:12]))
    source=qianchuan.save_snapshot(rows,meta,raw_dir)
    provenance=qianchuan.provenance(source)
    result={'report_kind':'context_historical_facts','source_api':provenance,
            'source_file':source.relative_to(root).as_posix(),'audit':audit,'totals':totals,'plans':plans,
            'notes':['只读接口历史事实缓存，不是已发送日报。利润和退款假设由v28上下文另行核算。']}
    target=sources.inside(root,home/'02_data/history'/('model_'+account+'_'+day+'_'+uuid.uuid4().hex[:12]+'.json'))
    context.write_json(target,result)
    return {'created':True,'date':day,'source':target.relative_to(root).as_posix(),'plans':len(plans),
            'scope':'乘方商品计划自然日事实，未发飞书、未计算旧模型利润'}


def refresh_budgets(root,account,env,*,now=None):
    from . import budgets
    now=clock(now);root=Path(root).resolve();validate_account(root,account,env)
    home=context.initialize(root,account);rules,_=sources.load_json(sources.inside(root,home/'01_rules/rules.json'))
    gaps=[];current=sources.current(root,account,now,rules,gaps)
    if (current['totals'] is None or 'future_snapshot_excluded' in gaps or not current.get('cutoff')
            or now-sources.instant(current['cutoff'])>timedelta(minutes=gate.stale_minutes(root))):
        raise ValueError('最新累计来源无效、过期或含未来观察点；未选择旧计划刷新预算')
    ids=[{'plan_id':str(p['id'])} for p in current['plans']]
    if not ids or len(ids)>1000:raise ValueError('计划列表为空或超过1000，需人工核对范围')
    snapshot=budgets.fetch(env,account,ids)
    if snapshot.get('advertiser_id')!=account or snapshot.get('kind')!='current_budget':raise ValueError('预算来源不符')
    fetched=sources.instant(snapshot['fetched_at']);completed=clock(None)
    # The real fetch can finish after the command start; compare with its completion time.
    if not now-timedelta(minutes=1)<=fetched<=completed:raise ValueError('预算读取时间无效')
    expected={p['plan_id'] for p in ids};seen=set();ok=0
    for row in snapshot['plans']:
        pid=str(row['plan_id'])
        if pid not in expected or pid in seen:raise ValueError('预算响应计划ID与本次请求不一致')
        seen.add(pid);at=sources.instant(row['fetched_at'])
        if not now-timedelta(minutes=1)<=at<=completed:raise ValueError('逐计划预算时间无效')
        if row['status']=='ok':
            if row.get('mode') not in budgets.MODES:raise ValueError('预算模式未知')
            if row.get('mode')!='BUDGET_MODE_INFINITE':finance.number(row['amount'])
            ok+=1
        elif row['status']!='unavailable':raise ValueError('预算响应状态无效')
    if seen!=expected:raise ValueError('预算响应缺少计划，未保存不完整快照')
    path=budgets.save(snapshot,sources.inside(root,root/'data/qianchuan'))
    return {'source':path.relative_to(root).as_posix(),'requested':len(ids),'successful':ok,'unavailable':len(ids)-ok,
            'scope':'仅本次有效累计快照中的乘方计划；不是全部商品视频计划预算'}
