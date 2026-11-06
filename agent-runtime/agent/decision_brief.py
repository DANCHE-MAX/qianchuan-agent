"""Deterministic attention priorities, not bidding instructions or financial audit."""
from decimal import Decimal


def number(value):
    if value is None:return None
    value=Decimal(str(value))
    return value if value.is_finite() else None


def rank(report,mapping_day=None):
    mapping={}
    if mapping_day is not None:
        if mapping_day.get('day')!=report['date'] or str(mapping_day.get('account'))!=str(report['account']):
            raise ValueError('Decision mapping scope mismatch')
        for row in mapping_day['rows']:
            pid=str(row['plan']);value=(row.get('product'),row.get('name'))
            if pid in mapping and mapping[pid]!=value:raise ValueError('Conflicting decision mapping')
            mapping[pid]=value
    groups={};seen=set()
    for row in report['rows']:
        pid=str(row['plan_id'])
        if pid in seen:raise ValueError('Duplicate decision plan')
        seen.add(pid)
        product,name=mapping.get(pid,(None,None))
        managed='全店托管' in str(row.get('plan_name') or row.get('name',''))
        key='managed' if managed else 'product:'+str(product) if product and name else 'plan:'+pid
        label='全店托管' if managed else name if product and name else (row.get('name') or '名称待核验')+'（计划）'
        group=groups.setdefault(key,dict(id=key,name=label,profit=Decimal(0),spend=Decimal(0),plans=[],imputed=0))
        group['plans'].append(pid);group['imputed']+=row.get('gmv_source')=='business_rule_zero_spend'
        for metric in ('profit','spend'):
            value=number(row.get(metric))
            group[metric]=None if value is None or group[metric] is None else group[metric]+value
    values=list(groups.values())
    def ordered(metric,predicate,reverse=False):
        selected=[g for g in values if g[metric] is not None and predicate(g[metric])]
        selected.sort(key=lambda g:(-g[metric] if reverse else g[metric],g['id']))
        return [{**g,**{k:None if g[k] is None else f'{g[k]:.2f}' for k in ('profit','spend')}} for g in selected[:3]]
    return dict(profit=ordered('profit',lambda n:n>0,True),loss=ordered('profit',lambda n:n<0),
                spend=ordered('spend',lambda n:n>0,True),pending=sum(g['profit'] is None or g['spend'] is None for g in values),
                imputed=sum(g['imputed'] for g in values),mapping_known=bool(mapping))


def actions(report,mapping_day=None):
    result=rank(report,mapping_day);items=[]
    for key,title,metric,empty in [('profit','优先关注盈利','profit','暂无已知盈利项'),('loss','优先查看亏损','profit','暂无已知亏损项'),('spend','关注主要消耗','spend','暂无已知正消耗项')]:
        rows=result[key]
        detail='；'.join(f'{i+1}. {r["name"]} {r[metric]}元' for i,r in enumerate(rows)) if rows else empty
        items.append(dict(title=title,detail=detail+'。',action='粗略模型排序；待核验项不参与该指标排名，不代表可追加预算。'))
    items[-1]['action']=f'{result["pending"]}项数据待核验；{result["imputed"]}个计划含业务规则补值。只供人工判断，不自动调预算。'
    return items


def question(command):
    if any(w in command for w in ('周报','这周','本周','近一周','最近一周','今天','今日','现在','当前','预算','追加','为什么','原因')):return None
    if any(w in command for w in ('亏得最多','亏损最多','亏损最大','最亏','亏最多')):return 'loss'
    if any(w in command for w in ('赚得最多','赚最多','最赚钱','盈利最多','利润最高','表现最好')):return 'profit'
    if any(w in command for w in ('花在哪里','花哪','消耗最多','广告费最多','钱花在哪')):return 'spend'
    return None


def answer(command,report):
    key=question(command)
    if key is None:return None
    result=rank(report,report.get('_decision_mapping'));rows=result[key];metric='spend' if key=='spend' else 'profit'
    label={'profit':'预估盈利靠前','loss':'预估亏损靠前','spend':'广告消耗靠前'}[key]
    detail='；'.join(f'{i+1}. {r["name"]} {r[metric]}元' for i,r in enumerate(rows)) or '暂无可排名记录'
    return f'数据日期：{report["date"]}（北京时间）。{label}：{detail}。\n粗略估计模型，非最终财务净利润；{result["pending"]}项待核验，{result["imputed"]}个计划含业务规则补值。未关联商品按计划分别展示；历史排序不是今天可执行预算。'
