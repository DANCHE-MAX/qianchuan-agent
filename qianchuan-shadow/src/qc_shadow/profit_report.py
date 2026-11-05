"""Local text/CSV presentation only; deliberately no messaging dependency."""
from decimal import Decimal

COLUMNS=[('款号','style'),('退款前GMV','gross_income'),('投前贡献利润','pre_ad_profit'),
    ('实际广告费（计划报表）','actual_ad_spend'),('15%估算广告费','estimated_ad_spend'),
    ('扣实际广告后的预估利润','post_ad_profit_actual'),('扣15%广告后的预估利润','post_ad_profit_estimated'),
    ('仅保本最多还能承受','break_even_additional_spend'),('按15%且保本建议还能投','recommended_additional_spend')]
def shown(v):return '待核验' if v is None else str(v)
def comparison_rows(result):return [{label:r.get(key) for label,key in COLUMNS} for r in result['products']]
def comparison_report(result):
    p=result['profile'];rows=result['products']
    lines=['# 三款广告费用与利润对比（v3）','',f"统计期间：{p['start']}至{p['end']}；账户：{p['account_id']}；单位：元。",'',
        '实际广告费来自运营单商品计划报表，尚未完成账户财务对账。收入、退款及成本沿用该批订单快照，未更新至今天。两种广告费分别计算，不叠加扣除。','',
        '| '+' | '.join(label for label,key in COLUMNS)+' |','|'+'|'.join(['---']+['---:']*(len(COLUMNS)-1))+'|']
    for r in rows:lines.append('| '+' | '.join(shown(r.get(k)) for _,k in COLUMNS)+' |')
    totals=['合计']
    for _,key in COLUMNS[1:]:
        values=[r.get(key) for r in rows];totals.append(f"{sum(Decimal(v) for v in values):.2f}" if all(v is not None for v in values) else '待核验')
    lines.extend(['| '+' | '.join(totals)+' |','','## 如何理解','','- 扣实际广告后的利润＝投前贡献利润－同期实际计划消耗。','- 扣15%广告后的利润＝投前贡献利润－退款前GMV×15%。','- 保本最多还能承受＝max(0,投前贡献利润－同期实际广告费)。这是把历史剩余利润用于广告的理论上限。','- 按15%且保本建议还能投＝max(0,min(GMV×15%,投前贡献利润)－同期实际广告费)。先逐款计算，再合计，不跨款挪用额度。','- 已经超过15%额度的款建议增加0；已经亏损的款保本剩余额度也为0，真实亏损金额仍显示。','- 缺少实际广告日数据时，实际利润及两类追加额度显示待核验，不补0；15%情景仍可估算。','','## 提醒与执行','','沿用“扣15%广告后利润严格大于GMV的15%才达标”的规则。提醒草稿展示全部3款并逐款注明是否达标，不新增自动发送触发器。群与人员未配置，草稿未发送，无预算动作。','','## 适用边界','','这是同一历史期间、假设收入与其他成本不再变化时的额度，不是今天的实时可投预算。新增投放前应更新订单、售后及实际消耗。实际报表消耗不等于已完成财务结算核验；所有利润仍含默认退回和估算费用假设，未覆盖人员租金及未提供的变动成本。',f"\n模型版本：{result['model_version']}；配置版本：{p['version']}。"])
    return '\n'.join(lines)
def reminder_draft(result):
    p=result['profile'];lines=['# 群内提醒草稿（未发送）','','@投手（待配置） @老板（待配置）',f"{p['start']}至{p['end']}三款测算：",'']
    for r in result['products']:
        lines.extend([f"**{r['style']}**",f"已花{shown(r.get('actual_ad_spend'))}元广告费，扣后预估剩{shown(r.get('post_ad_profit_actual'))}元。",
            f"若按GMV的15%花{shown(r.get('estimated_ad_spend'))}元，扣后预估剩{shown(r.get('post_ad_profit_estimated'))}元。",
            f"只求保本，最多还能承受{shown(r.get('break_even_additional_spend'))}元；同时按15%规则，建议还能投{shown(r.get('recommended_additional_spend'))}元。",
            '按15%广告费情景，利润未超过GMV的15%，需关注。' if r.get('alert_required') else '按15%广告费情景，利润目标达标。' if r.get('target_met') else '利润目标待核验。'])
        over=r.get('actual_over_estimated_ad')
        if over is not None and Decimal(over)>0:lines.append(f"实际已比15%广告额度多花{over}元，因此建议增加0元。")
        lines.append('')
    lines.extend(['以上是这14天的历史剩余额度，不是今天可直接追加的预算；0元表示不建议继续增加该期间广告总额。实际已花金额来自计划报表，尚未财务对账。','退款成功按默认退回处理，费用为估算；利润未扣人员、租金等固定费用。群与人员尚未配置，本草稿未发送，未修改预算。'])
    return '\n'.join(lines)
