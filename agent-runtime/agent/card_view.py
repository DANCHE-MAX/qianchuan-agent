"""Native Feishu card layouts. Never send model Markdown as the display format."""
import re
from .roi import payment_roi
from decimal import Decimal, InvalidOperation


def plain(value):
    text = str(value)
    text = re.sub(r'\\([*#_`~|>])', r'\1', text)
    text = re.sub(r'(?m)^\s*```[^\n]*$', '', text)
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', text)
    text = re.sub(r'(?m)^\s*>\s?', '', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1（\2）', text)
    for mark in ('**', '__', '~~', '`'):
        text = text.replace(mark, '')
    text = re.sub(r'(?m)^\s*[-*+]\s+', '• ', text)
    return text.strip()


def text_element(value):
    return {'tag': 'div', 'text': {'tag': 'plain_text', 'content': plain(value) or '—'}}


def fields(pairs):
    """Feishu native two-column fields; no Markdown table syntax."""
    return {'tag': 'div', 'fields': [
        {'is_short': True, 'text': {'tag': 'plain_text', 'content': plain(label) + '\n' + plain(value)}}
        for label, value in pairs
    ]}


def card(title, elements):
    return {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue', 'title': {'tag': 'plain_text', 'content': plain(title)}},
            'elements': elements or [text_element('暂无内容')]}


def _cells(line):
    # Escaped pipes belong to the value, rather than separating columns.
    line = line.strip().strip('|')
    return [plain(c.strip()) for c in re.split(r'(?<!\\)\|', line)]


def _separator(line):
    cells = _cells(line)
    return len(cells) >= 2 and all(re.fullmatch(r':?-{3,}:?', c.replace(' ', '')) for c in cells)


def answer_elements(content, max_elements=38):
    """Convert headings, lists and Markdown tables into native text/field components."""
    lines = str(content).splitlines()
    elements, paragraph = [], []

    def flush():
        if paragraph:
            value = plain('\n'.join(paragraph))
            if value:
                elements.append(text_element(value))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if i + 1 < len(lines) and '|' in line and _separator(lines[i + 1]):
            flush()
            headers = _cells(line)
            i += 2
            while i < len(lines) and '|' in lines[i] and lines[i].strip():
                values = _cells(lines[i])
                if len(values) != len(headers):
                    elements.append(text_element('；'.join(values)))
                else:
                    # Each data row stays together, with explicit column labels.
                    elements.append(text_element(values[0]))
                    for offset in range(1, len(headers), 2):
                        elements.append(fields(list(zip(headers, values))[offset:offset+2]))
                i += 1
            continue
        if re.fullmatch(r'\s*(```.*|---+|\*\*\*+)\s*', line):
            flush()
        elif re.match(r'^#{1,6}\s+', line) or re.fullmatch(r'\*\*[^*]+\*\*[:：]?', line):
            flush()
            if elements:
                elements.append({'tag': 'hr'})
            elements.append(text_element('【' + plain(line) + '】'))
        elif not line:
            flush()
        else:
            paragraph.append(line)
        i += 1
    flush()
    if len(elements) > max_elements:
        elements = elements[:max_elements-1] + [text_element('内容较长，本卡片展示部分结果；请指定某个计划或问题继续查看。')]
    return elements


def answer_card(content, title='投流助手｜问答'):
    return card(title, answer_elements(content))


def amount(value, unit='元'):
    if value is None:
        return '—'
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return '—'
        from decimal import ROUND_HALF_UP
        return format(number.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), ',.2f') + (' ' + unit if unit else '')
    except (InvalidOperation, ValueError, TypeError):
        return '—'


def period(report):
    a = report['audit']
    season = {'autumn_winter': '秋冬', 'summer': '夏季'}.get(report.get('season'), '')
    return '%s 至 %s｜%s｜%s个完整日' % (a['start'], a['end'], season, a['days'])


def summary_elements(report):
    t = report['totals']
    result = [text_element(period(report)), text_element('【后台实测】')]
    items = [('毛成交', 'gmv'), ('广告消耗', 'ad_spend'), ('后台净成交', 'backend_net'), ('综合成本', 'comprehensive_cost')]
    for i in range(0, len(items), 2):
        result.append(fields([(label, amount(t.get(key))) for label, key in items[i:i+2]]))
    result += [fields([('整体支付 ROI', amount(payment_roi(t), ''))]),
               {'tag': 'hr'}, text_element('【历史退款情景预测】'),
               fields([('预测最终净成交', amount(t.get('predicted_net'))), ('模型预计利润', amount(t.get('predicted_profit')))]),
               fields([('估计总成本', amount(t.get('estimated_total_cost')))]),
               {'tag': 'hr'}, text_element('【日均消耗参考】'),
               fields([('实际日均消耗', amount(t.get('daily_spend'))), ('GMV 15%参考', amount(t.get('daily_cap_reference')))]),
               fields([('固定成交规模下保本参考', amount(t.get('daily_breakeven_spend')))]),
               text_element('预测利润不是结算利润；日消耗参考不是后台预算设置或调预算建议。')]
    return result


def plan_elements(plans, detailed=False):
    from .budgets import describe
    result = []
    for p in plans:
        result += [{'tag': 'hr'}, text_element('%s（%s）\n%s' % (p.get('plan_name', ''), p.get('plan_id', ''), describe(p))),
                   fields([('毛成交｜实测', amount(p.get('gmv'))), ('广告消耗｜实测', amount(p.get('ad_spend')))]),
                   fields([('整体支付 ROI｜实测', amount(payment_roi(p), '')), ('预计利润｜预测', amount(p.get('predicted_profit')))])]
        if detailed:
            refund = p.get('refund_rate')
            refund = '未配置' if refund is None else amount(Decimal(str(refund)) * 100, '%')
            result += [fields([('历史退款率假设', refund), ('实际日均消耗', amount(p.get('daily_spend')))]),
                       fields([('GMV 15%日参考', amount(p.get('daily_cap_reference'))), ('固定成交保本日参考', amount(p.get('daily_breakeven_spend')))]),
                       text_element('判断：%s。' % p.get('status', '待核对'))]
    return result


def report_reply(report, analysis='', plans_only=False, compact=False):
    if plans_only:
        elements = [text_element(period(report))] + plan_elements(report.get('plans', [])[:8], detailed=True)
        title = '投流助手｜计划数据'
    else:
        elements = summary_elements(report)
        title = '投流助手｜日报分析' if analysis else '投流助手｜日报数据'
        if not compact:
            elements += [text_element('【逐计划对照】')] + plan_elements(report.get('plans', [])[:8])
    if analysis:
        elements += [{'tag': 'hr'}, text_element('【分析结论】')] + answer_elements(analysis, max_elements=15)
    if len(report.get('plans', [])) > 8:
        elements.append(text_element('仅展示前8个计划，其他计划请用“计划 名称”查询。'))
    elements.append(text_element('所有金额单位：元。退款与利润为历史情景测算，不是当期实退或最终结算。'))
    return card(title, elements)


def reformat_card(content):
    """Reformat only the previous answer, without expanding its source data."""
    elements = [text_element('将上一轮回答按条目展示，未新增分析。')]
    clean = plain(content)
    parts = [p.strip() for p in re.split(r'\n\s*\n|\n(?=\d+[.、．]\s*)', clean) if p.strip()]
    for index, part in enumerate(parts[:16], 1):
        elements.append(fields([('条目', str(index)), ('内容', part)]))
    if len(parts) > 16:
        elements.append(text_element('其余内容：' + '\n\n'.join(parts[16:])))
    return card('投流助手｜上一轮内容表格', elements)


def budget_card(plans, scope_note):
    from .budgets import describe
    elements = [text_element(scope_note)]
    for p in plans[:8]:
        elements += [text_element('%s（%s）' % (p.get('plan_name', ''), p['plan_id'])),
                     text_element(describe(p))]
    if len(plans) > 8:
        elements.append(text_element('本卡片展示前8条计划；其他计划请用“预算 计划名称或ID”查询。'))
    return card('投流助手｜后台预算', elements)
