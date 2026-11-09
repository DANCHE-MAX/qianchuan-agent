"""One deterministic guide image in the existing scheduled bundle; no new sender."""
import hashlib,json,sqlite3,subprocess
from pathlib import Path
from datetime import date,timedelta
from decimal import Decimal
from . import hourly,profit_daily,data_completeness


def _ledger_previous(root,account,day):
    db=Path(root)/'data/qianchuan/daily_brief.db'
    if not db.exists():return None
    try:
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
            row=conn.execute('SELECT payload FROM jobs WHERE account=? AND day=? AND sent=1',(account,day)).fetchone()
        if not row:return None
        bundle=json.loads(row[0])
        if bundle.get('type')!='profit_bundle' or str(bundle.get('account'))!=account or bundle.get('date')!=day:return None
        result=json.loads((Path(bundle['directory'])/'result.json').read_text())
        if str(result.get('account'))!=account or result.get('date')!=day:return None
        return result
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):return None


def previous(root,account,day):
    """Prefer completed ledger; otherwise exact-day verified report artifacts only."""
    result=_ledger_previous(root,account,day)
    if result is not None:return result
    candidates=[]
    for folder in (Path(root)/'out/profit_daily'/account/day).glob('*'):
        try:
            receipt=json.loads((folder/'delivery.json').read_text())
            parts=receipt.get('parts',{})
            if not receipt.get('complete') or len(parts)<3 or not all(p.get('verified') and p.get('message_id') for p in parts.values()):continue
            if not (folder/('profit-'+day+'.xlsx')).is_file() or not list(folder.glob('profit-'+day+'-*.png')):continue
            report=json.loads((folder/'result.json').read_text())
            if str(report.get('account'))!=account or report.get('date')!=day:continue
            rows=report['rows'];known=[r for r in rows if r.get('profit') is not None]
            if len({str(r['plan_id']) for r in rows})!=len(rows):continue
            if len(known)!=report['calculable'] or len(rows)-len(known)!=report['missing_profit']:continue
            from . import plan_profit
            for row in known:
                if plan_profit.calculate(row['gmv'],row['spend'],row.get('policy',report['policy']))['profit']!=row['profit']:raise ValueError('History profit mismatch')
            for key in ('gmv','spend','profit'):
                expected=sum((amount(r[key]) for r in known),Decimal(0)) if known else None
                if amount(report['totals_calculable_only'][key])!=expected:raise ValueError('History totals mismatch')
            candidates.append(report)
        except (OSError,ValueError,KeyError,TypeError,ArithmeticError):continue
    # Multiple independent revisions require explicit reconciliation, not latest-file guessing.
    return candidates[0] if len(candidates)==1 else None


def amount(value):
    if value is None:return None
    n=Decimal(str(value))
    if not n.is_finite():raise ValueError('Non-finite guide amount')
    return n


def show(value):
    return '待核验' if value is None else f'{amount(value):,.2f}'


def build(current,prior,today,*,coverage_day=None,include_coverage=False,coverage_version=2,decision_version=0):
    day=current['date'];before=(date.fromisoformat(day)-timedelta(days=1)).isoformat()
    if (date.fromisoformat(today)-timedelta(days=1)).isoformat()!=day:raise ValueError('Guide must describe yesterday')
    comparable=bool(prior and prior.get('date')==before and str(prior.get('account'))==str(current['account']) and prior.get('policy')==current.get('policy'))
    totals=current['totals_calculable_only'];old=prior['totals_calculable_only'] if comparable else dict.fromkeys(totals)
    changes={k:(f'{amount(totals[k])-amount(old[k]):.2f}' if totals[k] is not None and old.get(k) is not None else None) for k in ('gmv','spend','profit')}
    oldrows={str(r['plan_id']):r for r in prior['rows']} if comparable else {}
    candidates=[]
    for row in current['rows']:
        value=amount(row.get('profit'));prev=oldrows.get(str(row['plan_id']),{});previous_profit=amount(prev.get('profit'))
        if value is None:continue
        change=value-previous_profit if previous_profit is not None else None
        if (change is not None and change<0) or value<0:
            candidates.append((change if change is not None and change<0 else value,row,prev))
    candidates.sort(key=lambda item:(item[0],str(item[1]['plan_id'])))
    # First the largest deterioration, then the largest remaining loss.
    selected=candidates[:1]
    rest=[c for c in candidates[1:] if amount(c[1]['profit'])<0]
    if rest:selected.append(min(rest,key=lambda c:amount(c[1]['profit'])))
    elif len(candidates)>1:selected.append(candidates[1])
    actions=[]
    for _,row,prev in selected:
        name=row.get('plan_name') or row.get('name') or '商品名待核验'
        if 'ops-' in name:name=row.get('name') or '商品名待核验'
        if '全店托管' in name:name='全店托管'
        actions.append({'title':name+'：核查利润变化','detail':f'预估利润 {show(prev.get("profit"))} → {show(row["profit"])}元；昨日广告费 {show(row.get("spend"))}元。','action':'对照成交、优惠及投放时段核查；变化原因待核验。'})
    if len(actions)<2:
        known=[r for r in current['rows'] if r.get('spend') is not None]
        if known:
            top=max(known,key=lambda r:amount(r['spend']))
            actions.append({'title':'核对广告消耗与成交','detail':f'昨日最大已知计划消耗 {show(top["spend"])}元；请在原Excel核查计划明细。','action':'仅为人工核查项，不据此自动增加或减少预算。'})
    while len(actions)<2:
        actions.append({'title':'核对昨日经营数据','detail':'可用计划数据不足，变化与原因待核验。','action':'核对原报表与采集覆盖，不用缺失值推算投放额度。'})
    missing=sum(r.get('profit') is None for r in current['rows'])
    zero=sum(r.get('profit') is None and amount(r.get('spend'))==0 for r in current['rows'])
    unknown_spend=sum(r.get('spend') is None for r in current['rows'])
    actions.append({'title':f'补齐利润缺口：{missing}个计划待核验' if missing else '复核利润估算口径',
                    'detail':f'{zero}个已知零消耗计划利润待核验；{unknown_spend}个计划广告费待核验。' if missing else '计划均可测算，但仍为模型利润，需实际退款、成本及费用核验。',
                    'action':'补核GMV、费用与缺失报表；完整账户利润不能用已知部分替代。' if missing else '按实际退款及成本对账，未确认前不称最终财务净利润。'})
    view={'date':day,'previous_date':before,'today':today,'current':totals,'previous':old,'changes':changes,'comparable':comparable,
            'calculable':current['calculable'],'previous_calculable':prior.get('calculable') if comparable else None,'missing':missing,
            'actions':actions,'policy':current.get('policy',{}),'as_of':current.get('as_of','待核验'),
            'previous_as_of':prior.get('as_of','待核验') if comparable else '待核验','warnings':current.get('warnings',[])}

    if decision_version:
        from . import decision_brief
        view['decision_version']=decision_version
        view['actions']=decision_brief.actions(current,coverage_day)
    if include_coverage:view['coverage']=data_completeness.build(current,coverage_day,version=coverage_version)
    return view


def attach(root,env,now,bundle):
    root=Path(root).resolve();out=Path(bundle['directory'])
    if (out/'delivery.json').exists() or bundle.get('guide_version'):raise ValueError('Never reorder a frozen bundle')
    local=now.astimezone(hourly.CHINA)
    main=json.loads((out/'result.json').read_text())
    if str(main['account'])!=str(bundle['account']) or main['date']!=bundle['date']:raise ValueError('Guide scope mismatch')
    before=(date.fromisoformat(main['date'])-timedelta(days=1)).isoformat()
    prior=previous(root,str(main['account']),before)
    mapping_path=out/'two_day/collected.json'
    mapping_days=json.loads(mapping_path.read_text()) if mapping_path.exists() else []
    coverage_day=next((d for d in mapping_days if d.get('day')==main['date']),None)
    view=build(main,prior,local.date().isoformat(),coverage_day=coverage_day,include_coverage=True,decision_version=1)
    folder=out/'guide';folder.mkdir(parents=True,exist_ok=True);source=folder/'guide.json';target=folder/'guide.png'
    profit_daily.save(source,view)
    profit_daily.save(folder/'previous.json',prior)
    cfg=json.loads((root/'config/profit_daily.json').read_text())
    subprocess.run([cfg['render_python'],str(root/'tools/daily_guide_image.py'),str(source),str(target),cfg['font']],check=True,capture_output=True,timeout=90)
    item={'kind':'image','path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    result=dict(bundle);result['files']=[item,*bundle['files']];result['guide_version']=1
    result['actors']=list(dict.fromkeys(bundle['actors']))
    result['text']=f'【每日投放简报｜{bundle["date"]}】导读、图表与Excel如下；缺失标待核验。'
    return result
