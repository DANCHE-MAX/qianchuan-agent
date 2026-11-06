"""Local report coverage, derived from evidence; no account-wide completeness claim."""
from decimal import Decimal


def build(report, mapping_day=None):
    rows=report['rows'];ids=[str(r['plan_id']) for r in rows]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate coverage plan')
    mapping={}
    if mapping_day is not None:
        if mapping_day.get('day')!=report['date'] or str(mapping_day.get('account'))!=str(report['account']):
            raise ValueError('Coverage mapping scope mismatch')
        for row in mapping_day['rows']:
            key=str(row['plan']);value=(row.get('product'),row.get('name'))
            if key in mapping and mapping[key]!=value:raise ValueError('Conflicting product mapping')
            mapping[key]=value
    counts=dict(linked=0,managed=0,unlinked=0,pending=0);details=[]
    missing={k:sum(r.get(k) is None for r in rows) for k in ('gmv','spend','profit')}
    for row in rows:
        pid=str(row['plan_id']);name=row.get('plan_name') or row.get('name') or '名称待核验'
        if '全店托管' in name:category='managed'
        elif pid not in mapping:category='pending'
        else:category='linked' if all(mapping[pid]) else 'unlinked'
        counts[category]+=1
        fields=[k for k in missing if row.get(k) is None]
        details.append({'plan_id':pid,'name':name,'missing_fields':fields,'product_mapping':category,
                        'status':'待核验' if fields or category in ('pending','unlinked') else '模型可测算'})
    total=len(rows);known=total-missing['profit']
    percent=str((Decimal(known)*100/total).quantize(Decimal('.01'))) if total else None
    sentence=(f'本次报表共{total}个计划，{known}个可测算、{missing["profit"]}个利润待核验；非全账户最终利润。'
              if total else '本次报表暂无计划明细，覆盖情况待核验；不能视为零消耗或零利润。')
    return {'version':1,'date':report['date'],'as_of':report.get('as_of') or '待核验',
            'total_plans':total,'calculable_plans':known,'pending_plans':missing['profit'],
            'calculable_percent':percent,'account_coverage_percent':None,
            'missing_fields':missing,'known_zero_spend_plans':sum(r.get('spend') is not None and Decimal(str(r['spend']))==0 for r in rows),
            'product_mapping':counts,'plans':details,'sentence':sentence,
            'scope':'本次报表计划清单；全账户覆盖率待核验。字段缺失可重叠，商品归属未知不等于金额未知。'}


def text_lines(view):
    if view is None:return ['数据完整性：待核验，输入不可读取。']
    m=view['missing_fields'];p=view['product_mapping']
    lines=['数据完整性：'+view['sentence'],
            '报表内可测算比例：'+(view['calculable_percent']+'%' if view['calculable_percent'] is not None else '待核验')+'；全账户覆盖率：待核验',
            f'缺失字段（可重叠）：GMV {m["gmv"]}，广告消耗 {m["spend"]}，利润 {m["profit"]}；已知零消耗 {view["known_zero_spend_plans"]}',
            f'商品归属：已关联 {p["linked"]}，全店托管 {p["managed"]}，未关联 {p["unlinked"]}，映射证据待核验 {p["pending"]}',
            '数据截至：'+str(view['as_of']),view['scope']]

    labels={'gmv':'GMV','spend':'广告消耗','profit':'利润'}
    mapping={'linked':'已关联','managed':'全店托管','unlinked':'未关联，待核验','pending':'映射证据待核验'}
    lines+=['计划缺口明细（仅留本地）：']
    for row in view['plans']:
        if row['status']=='待核验':
            fields='、'.join(labels[k] for k in row['missing_fields']) or '无金额缺失'
            lines.append(f"{row['name']}（{row['plan_id']}）：{fields}；{mapping[row['product_mapping']]}")
    return lines
