"""Account-wide scenario reporting. Pure calculations; no messaging or budget writes."""
from decimal import Decimal, ROUND_HALF_UP
from html import escape

VERSION = 'qianchuan-plan-scenario-v1'


def number(value):
    n = Decimal(str(value))
    if not n.is_finite() or n < 0:
        raise ValueError('Expected a finite nonnegative number')
    return n


def money(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def calculate(gmv, spend, policy):
    """All business parameters must come from a separately supplied local policy."""
    rate = number(policy['refund_rate'])
    multiplier = number(policy['purchase_multiplier'])
    fee_rate = number(policy['fee_rate'])
    ad_rate = number(policy['ad_rate'])
    if rate > 1 or multiplier == 0 or fee_rate > 1 or ad_rate > 1:
        raise ValueError('Invalid policy')
    if policy.get('shipping_mode') != 'excluded':
        raise ValueError('This scenario requires explicit shipping exclusion')
    if gmv is None or spend is None:
        return {k: None for k in ('net', 'cost', 'fee', 'preprofit', 'profit',
                                  'ad15', 'profit15', 'headroom', 'candidate')}
    gmv, spend = number(gmv), number(spend)
    net = money(gmv * (1-rate))
    cost, fee = money(net / multiplier), money(net * fee_rate)
    preprofit = net-cost-fee
    profit, ad15 = preprofit-spend, money(gmv*ad_rate)
    values = dict(net=net, cost=cost, fee=fee, preprofit=preprofit, profit=profit,
                  ad15=ad15, profit15=preprofit-ad15, headroom=max(Decimal(0), profit),
                  candidate=max(Decimal(0), min(preprofit, ad15)-spend))
    return {k: str(money(v)) for k, v in values.items()}


ZERO_GMV_RULE = 'confirmed-zero-spend-rechecked-gmv-v1'


def zero_gmv_eligible(plan, evidence, day):
    evidence=evidence or {};proof=evidence.get('gmv_recheck') or {}
    return (plan.get('goal')=='VIDEO_PROM_GOODS' and evidence.get('goal')=='VIDEO_PROM_GOODS'
            and str(plan.get('plan_id'))==str(evidence.get('plan_id')) and evidence.get('date')==day
            and bool(evidence.get('request_id')) and proof.get('date')==day
            and proof.get('success') is True and proof.get('gmv_missing') is True
            and bool(proof.get('request_ids')) and all(proof['request_ids']))


def report(inventory, daily, no_spend, policy, *, date):
    """Rows must be scoped to the same account/date by the acquisition layer.

    no_spend: successful fully paginated VIDEO_PROM_GOODS having_cost=NO evidence,
    each with plan_id, date, goal and request_id. Absence is never evidence of zero.
    """
    plans = {}
    for row in inventory:
        pid = str(row['plan_id'])
        if pid in plans and plans[pid] != row:
            raise ValueError('Conflicting inventory plan')
        plans[pid] = dict(row)
    details = {}
    for row in daily:
        pid = str(row['plan_id'])
        if row['date'] != date or pid in details:
            raise ValueError('Wrong date or duplicate plan across report topics')
        details[pid] = row
        plans.setdefault(pid, {'plan_id': pid, 'name': row.get('name') or '未命名计划'})
    zeros = {}
    for evidence in no_spend:
        if (evidence['date'] != date or evidence['goal'] != 'VIDEO_PROM_GOODS'
                or not evidence.get('request_id')):
            raise ValueError('Invalid zero-spend evidence')
        zeros[str(evidence['plan_id'])] = evidence
    rows = []
    for pid, plan in plans.items():
        raw = details.get(pid)
        gmv, spend = (raw.get('gmv'), raw.get('spend')) if raw else (None, None)
        if gmv is not None: gmv = str(money(number(gmv)))
        if spend is not None: spend = str(money(number(spend)))
        if pid in zeros:
            if spend is not None and number(spend) != 0:
                raise ValueError('Daily spend contradicts zero-spend evidence')
            spend = '0.00'
        raw_gmv=gmv
        imputed=gmv is None and spend=='0.00' and zero_gmv_eligible(plan,zeros.get(pid),date)
        if imputed:gmv='0.00'
        values = calculate(gmv, spend, policy)
        status = ('已取昨日明细' if values['profit'] is not None else
                  '已核实无消耗；GMV待核验' if pid in zeros else '昨日数据待核验')
        if imputed:status='业务规则补值：已核实零消耗，复查GMV缺失按0测算'
        rows.append(dict(plan, plan_id=pid, gmv=gmv, spend=spend, raw_gmv=raw_gmv,
                         gmv_source='business_rule_zero_spend' if imputed else 'api' if gmv is not None else 'missing',
                         gmv_rule_version=ZERO_GMV_RULE if imputed else None, **values,
                         status=status, current_budget=None,
                         evidence=zeros.get(pid) or (raw or {}).get('source')))
    rows.sort(key=lambda r: (r['profit'] is None, -Decimal(r['profit'] or '0'), r['name']))
    valid = [r for r in rows if r['profit'] is not None]
    totals = {key: str(sum((Decimal(r[key]) for r in valid), Decimal(0))) if valid else None
              for key in ('gmv', 'spend', 'profit')}
    return dict(version=VERSION, date=date, rows=rows, totals_calculable_only=totals,
                calculable=len(valid), missing_profit=len(rows)-len(valid),
                execution_enabled=False, feedback_required=False)


def reconcile(detail_values, aggregate):
    """An empty aggregate response is unknown, never a zero control total."""
    if aggregate is None or any(v is None for v in detail_values):
        return None
    return str(money(number(aggregate)-sum(map(number, detail_values), Decimal(0))))


def html_report(result):
    """Phone-friendly table preview; name color follows profit, missing stays gray."""
    cols = [('商品 / 计划名称', 'name'), ('实际广告费', 'spend'), ('预估净利润', 'profit'),
            ('退款前GMV', 'gmv'), ('15%广告参考', 'ad15'), ('按15%情景利润', 'profit15'),
            ('历史保本空间', 'headroom'), ('数据核查', 'status'),
            ('历史候选追加', 'candidate'), ('今日可执行追加', 'current_budget')]
    lines = ['<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
             '<style>body{font:16px sans-serif;color:#172b44}table{border-collapse:collapse}td,th{padding:12px;border-bottom:1px solid #ddd}th{background:#172b44;color:white}.profit{background:#14975d;color:white}.loss{background:#d83f45;color:white}.unknown{background:#e5e7eb}</style>',
             '<h2>'+escape(result['date'])+' 计划利润估算</h2>',
             '<p>历史情景估算，非今日预算。未扣固定费用及运费；仅已核实零消耗且复查GMV缺失的商品计划按业务规则补0，其他缺失待核验。无需反馈，不自动改预算。</p>',
             '<div style="overflow-x:auto"><table><tr>'+''.join('<th>'+t+'</th>' for t,k in cols)+'</tr>']
    for row in result['rows']:
        profit = row['profit']
        color = 'unknown' if profit is None or Decimal(profit)==0 else 'profit' if Decimal(profit)>0 else 'loss'
        cells = ['<td'+(' class="'+color+'"' if k=='name' else '')+'>'+escape(str(row.get(k) if row.get(k) is not None else '待核验'))+'</td>' for _,k in cols]
        lines.append('<tr>'+''.join(cells)+'</tr>')
    return '\n'.join(lines+['</table></div>'])
