"""千川模型的固定飞书日报卡片。仅负责展示，不改核算口径。"""

import json
from pathlib import Path

from .model import fmt


def _text(content, markdown=False):
    return {"tag": "div", "text": {"tag": "lark_md" if markdown else "plain_text",
                                 "content": str(content)}}


def _card(title, elements, color="blue"):
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
        "elements": elements,
    }


def _amount(item, key):
    return fmt(item.get(key))


def build(result, plans_per_card=4, active_only=False, max_plans=None):
    """Native summary fields plus paged plan fields; all values come from the model."""
    from .card_view import card, text_element, summary_elements, plan_elements, period
    if plans_per_card < 1:
        raise ValueError("每张计划卡至少展示一个计划")
    audit = result['audit']
    summary = summary_elements(result)
    summary.append(text_element('数据体检：采用 %s 行；%s' % (
        audit.get('selected_rows', '—'), audit.get('completeness_note', '导出范围需人工核对'))))
    window = '单日' if audit['days'] == 1 else '%s日窗口' % audit['days']
    built = [card('千川投流日报｜' + window, summary)]
    plans = result.get('plans', [])
    all_count = len(plans)
    if active_only:
        from decimal import Decimal
        plans = [p for p in plans if any(Decimal(str(p.get(k) or 0)) != 0
                 for k in ('gmv', 'ad_spend', 'backend_net', 'comprehensive_cost'))]
    eligible = len(plans)
    if max_plans is not None:
        if max_plans < 1:
            raise ValueError('卡片展示计划上限必须大于0')
        plans = plans[:max_plans]
    if active_only or max_plans is not None:
        summary.append(text_element('完整报告保留%d条计划；其中%d条有消耗或成交等经营数据；本次展示按消耗排序的前%d条。其余可用“计划 名称或ID”查询。整体合计仍覆盖全部记录。' %
                                    (all_count, eligible, len(plans))))
    total_pages = (len(plans) + plans_per_card - 1) // plans_per_card
    for offset in range(0, len(plans), plans_per_card):
        page = offset // plans_per_card + 1
        elements = [text_element('%s｜第%d/%d页｜按广告消耗排序' % (period(result), page, total_pages))]
        elements += plan_elements(plans[offset:offset+plans_per_card], detailed=True)
        elements.append(text_element('退款率与预计利润是历史情景测算，不是当期实退或最终结算。'))
        built.append(card('逐计划｜%d/%d' % (page, total_pages), elements))
    return built


def save(cards, model_report_path):
    path = Path(model_report_path).with_name(Path(model_report_path).stem + "_cards.json")
    path.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
