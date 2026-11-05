"""飞书长连接入站：群内 @ 分流报告查询和知识问答，不修改预算。"""

import json
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .notify import FeishuAppNotifier, feishu_allowed_chat_ids
from .settings import load_env
from .knowledge import Knowledge, tokens
from . import hourly, diagnostics
from .roi import normalize_report
from .conversation import Conversation, is_followup
from .card_view import report_reply, answer_card, reformat_card

BASE = Path(__file__).resolve().parent.parent


def build_event_handler(lark, on_message):
    def ignore_reaction(event):
        diagnostics.emit('reaction_ignored')
    return (lark.EventDispatcherHandler.builder('', '')
            .register_p2_im_message_receive_v1(on_message)
            .register_p2_im_message_reaction_created_v1(ignore_reaction).build())

HELP_SECTIONS = (
    ('日报', '生成指定日期的日报\n例如：生成10.22日报\n报告：查看最新已保存日报\n报告 秋冬／报告 夏季：按季节查看'),
    ('总结与表格', '总结日报：获取简短结论\n表格：查看日报明细\n表格显示：将上一轮回答转为表格'),
    ('余额与计划', '预算诊断：查看当前乘方商品计划的预算约束\n预算诊断 马甲：诊断指定计划\n余额：实时查询账户余额\n预算：查询最新报告内计划的当前预算\n预算 马甲：查询指定计划预算\n计划 托管：查看指定计划数据'),
    ('业务问答', '采集与速率：只读核验本地采集点、计划/素材区间消耗速率及四小时余额速率。\n健康检查／运行状态／数据检查：查看运行异常、采集时效和小时缺口。\n我的ID：查看自己的飞书open_id，供预警@人配置。\n直接输入业务问题，结合SOP与最新日报回答。\n包含“当前／目前／现在／今天”的问题使用今日已采集小时数据。\n也可继续追问，例如“再简单一点”。'),
)
HELP_NOTE = '在群内 @投流助手 后输入指令。生成日报会采集指定日期的数据；“报告”查看已保存结果。'
HELP = '投流助手可用指令\n\n' + '\n\n'.join(
    title + '\n' + body for title, body in HELP_SECTIONS) + '\n\n' + HELP_NOTE


def help_card():
    from .card_view import card, text_element
    elements = []
    for title, body in HELP_SECTIONS:
        if elements:
            elements.append({'tag': 'hr'})
        elements.extend([text_element('【' + title + '】'), text_element(body)])
    elements.extend([{'tag': 'hr'}, text_element(HELP_NOTE)])
    return card('投流助手｜使用帮助', elements)



def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _money(value):
    if value is None:
        return "未计算"
    try:
        return format(Decimal(str(value)), ",.2f")
    except InvalidOperation:
        return "未计算"


def _latest_report(outdir, season=None, account=None):
    candidates = []
    for path in Path(outdir).glob("model_*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            audit = report["audit"]
            if not all(k in audit for k in ("start", "end", "days")):
                continue
            if season and report.get("season") != season:
                continue
            if account and str((report.get("source_api") or {}).get("advertiser_id")) != str(account):
                continue
            candidates.append((audit["end"], path.stat().st_mtime, normalize_report(report)))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return max(candidates, default=(None, None, None))[-1]


def _header(report):
    a = report["audit"]
    season = {"autumn_winter": "秋冬", "summer": "夏季"}.get(report.get("season"), "未标季节")
    return "%s—%s（%s，%s个完整日）" % (a["start"], a["end"], season, a["days"])


def budget_term(command):
    command = command.strip(' 。！？!?')
    if command in ('预算', '查看预算', '计划预算', '所有计划预算', '查看所有计划预算'):
        return ''
    if command.startswith('预算 '):
        return command[3:].strip()
    match = re.fullmatch(r'(?:查看)?(.+?)计划预算(?:是多少)?', command)
    return match.group(1).strip() if match else None


def command_route(command):
    command = re.sub(r"\s+", " ", command.strip())
    if command.lower() in ("帮助", "菜单", "?", "？", "help", ""):
        return "help"
    if command in ("报告", "报告 秋冬", "报告 夏季", "模型", "概览"):
        return "report"
    if command == "计划" or command.startswith("计划 "):
        return "plan"
    return "knowledge"


def display_intent(command):
    text = re.sub(r"\s+", "", command).strip('。！？!?')
    if text in ('表格显示', '用表格显示', '表格展示', '用表格展示', '换成表格', '改成表格', '以表格形式显示'):
        return 'reformat'
    if text in ('表格', '日报表格', '数据表格') or ('表格' in text and ('日报' in text or '报告' in text)):
        return 'data'
    return 'answer'


def message_text(item):
    raw = item.get("body", {}).get("content", "{}")
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return ""
    def flatten(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(flatten(v) for v in value)
        if isinstance(value, dict):
            return "\n".join(flatten(v) for k, v in value.items() if k in ("text", "content", "title", "elements", "fields", "zh_cn", "en_us"))
        return ""
    return flatten(obj)[:10000]


def answer_chat(notifier, command, env, context, knowledge=None):
    if env.get("KNOWLEDGE_ENABLED", "").lower() != "true":
        return "知识问答尚未开启，请联系管理员。"
    chat_id = context["chat_id"]
    try:
        if context.get("parent_id"):
            items = notifier.get_message(context["parent_id"])
            label = "被引用的消息"
        else:
            end = int(context.get("create_time") or int(time.time()*1000)) // 1000
            data = notifier.recent_messages(chat_id, end)
            items = data.get("items", [])
            label = "提问前最近最多50条消息（不保证覆盖全部聊天）"
        items = [i for i in items if i.get("chat_id") == chat_id and not i.get("deleted")]
        if not context.get("parent_id") and "日报" in command:
            items = [i for i in items if "千川投流日报" in message_text(i)]
            items.sort(key=lambda i: int(i.get("create_time", 0)), reverse=True)
            items = items[:1]
            label = "最近最多50条消息中找到的上一张日报摘要卡（不包含其他计划卡）"
        selected = [{"消息ID": i.get("message_id"), "发送时间戳毫秒": i.get("create_time"),
                     "类型": i.get("msg_type"), "内容": message_text(i)} for i in items]
        selected = [i for i in selected if i["内容"]]
        if not selected:
            return "未能在当前群的查询范围内读到可分析的文字或日报卡片，请引用目标消息重试。图片和附件暂不识别。"
    except Exception:
        return "读取群消息失败，请管理员检查机器人是否在群内，以及消息读取和获取群组所有消息权限是否已发布生效。未改用本地日报代替。"
    payload = json.dumps({"来源范围": label, "消息": selected}, ensure_ascii=False)
    if len(payload) > 24000:
        selected = selected[:10]
        payload = json.dumps({"来源范围": label + "；仅取其中最近10条，单条最多10000字", "消息": selected}, ensure_ascii=False)
        if len(payload) > 24000:
            return "消息内容过长，请引用一条具体消息提问。"
    result = (knowledge or Knowledge()).ask(command, env, report_context=payload)
    return "分析范围：%s。\n%s" % (label, result.replace("依据本地已保存日报（不代表实时结果）。", "依据当前群读取的消息内容。").replace("本地模型日报", "群消息"))


def reply_command(notifier, message_id, command, outdir=None, env=None, context=None, memory=None):
    context = context or {}
    from . import feedback
    if feedback.matches(command):
        root = Path(outdir).parent if outdir else BASE
        return feedback.reply(notifier, message_id, command, root, env if env is not None else load_env(), context)
    from . import collection_rates
    if collection_rates.matches(command):
        root = Path(outdir).parent if outdir else BASE
        return collection_rates.reply(notifier, message_id, root, env if env is not None else load_env(), context)
    if command.strip(' 。！？!?') in ('健康检查', '运行状态', '数据检查'):
        from . import health
        root = Path(outdir).parent if outdir else BASE
        status = health.check(root, env or load_env())
        return notifier.reply_card(message_id, health.rendered(status))
    if command.strip(' 。！？!?') in ('我的ID', '我的id', '查询我的ID', '查询我的id'):
        allowed = set(feishu_allowed_chat_ids(env or {}) or [(env or {}).get('FEISHU_CHAT_ID')])
        if context.get('chat_id') not in allowed or not context.get('sender_id'):
            return None
        return notifier.reply_text(message_id, '你的飞书open_id：%s\n这是当前机器人应用下的身份ID，可填入config/rules.json的notify.roles对应角色名单。' % context['sender_id'])
    if command_route(command) == "help":
        return notifier.reply_card(message_id, help_card())
    from . import budget_diagnostics
    if budget_diagnostics.term(command) is not None:
        from .settings import load_config
        cfg = load_config(BASE / 'config' / 'rules.json').get('budget_diagnostics', {})
        return budget_diagnostics.reply(notifier, message_id, command, BASE,
                                        env if env is not None else load_env(), cfg, context, memory)
    from . import account_balance
    if account_balance.matches(command):
        return account_balance.reply(notifier, message_id, command, BASE,
                                     env if env is not None else load_env(), context, memory)
    from . import generation
    if generation.matches(command):
        return generation.reply(notifier, message_id, command, BASE, outdir or BASE / 'out',
                                env if env is not None else load_env(), context, memory)
    if not context.get('parent_id') and budget_term(command) is not None:
        from . import budgets
        from .card_view import budget_card
        report = _latest_report(outdir or BASE / 'out', account=(env or {}).get('QC_ADVERTISER_IDS'))
        if report is None:
            return notifier.reply_text(message_id, '当前账户没有可查询的报告，请先生成API日报；未使用其他账户的报告。')
        term = budget_term(command)
        plans = [dict(p) for p in report['plans'] if not term or
                 term in p.get('plan_name', '') or term == str(p['plan_id'])]
        if not plans:
            return notifier.reply_text(message_id, '最新报告中未找到该计划，请输入计划名称或ID。')
        try:
            snapshot = budgets.fetch(env or load_env(), (report.get('source_api') or {}).get('advertiser_id'), plans)
            budgets.save(snapshot, BASE / 'data' / 'qianchuan')
        except ValueError as exc:
            return notifier.reply_text(message_id, str(exc))
        selected = dict(report, plans=plans)
        budgets.attach(selected, snapshot)
        scope = '范围：%s报告内的%s条匹配计划；本次查询后台当前设置，不是该历史日期的预算。' % (report['audit']['end'], len(plans))
        result = notifier.reply_card(message_id, budget_card(plans, scope))
        budget_source = json.dumps({'范围': scope, '预算快照': snapshot}, ensure_ascii=False)
        budget_memory = memory or Conversation(BASE / 'data' / 'conversation.db', account=(env or {}).get('QC_ADVERTISER_IDS'))
        budget_memory.save(context.get('chat_id'), context.get('sender_id'), [
            {'question': command[:2000], 'answer': scope + '\n' + '\n'.join(budgets.describe(p) for p in plans),
             'source': budget_source, 'report_snapshot': None, 'display_kind': 'answer'}])
        return result
    kb = Knowledge()
    memory = memory or Conversation(BASE / "data" / "conversation.db", account=(env or {}).get("QC_ADVERTISER_IDS"))
    chat, user = context.get("chat_id"), context.get("sender_id")
    intent = display_intent(command)
    effective_command = command
    if intent == 'data':
        effective_command = '报告 夏季' if '夏季' in command else '报告 秋冬' if '秋冬' in command else '报告'
    route = command_route(effective_command)
    turns = []
    if intent == 'reformat' and not context.get('parent_id') and not hourly.is_current(command):
        turns = memory.load(chat, user)
        if not turns:
            return notifier.reply_markdown(message_id, '没有可转换的上一轮回答。请先查询“报告”或“总结日报”，也可以引用目标消息。')
        previous = turns[-1]
        snapshot = previous.get('report_snapshot')
        previous_route = command_route(previous.get('question', ''))
        legacy_kind = 'data' if previous_route == 'report' or display_intent(previous.get('question', '')) == 'data' else 'plan' if previous_route == 'plan' else 'answer'
        previous_kind = previous.get('display_kind', legacy_kind)
        if previous_kind in ('data', 'plan') and isinstance(snapshot, dict):
            rendered = report_reply(snapshot, plans_only=previous_kind == 'plan')
            rendered['header']['title']['content'] = '投流助手｜上一轮数据表格'
        else:
            rendered = reformat_card(previous['answer'])
        result = notifier.reply_card(message_id, rendered)
        memory.save(chat, user, turns + [dict(previous, question=command[:2000])])
        return result
    if hourly.is_current(command):
        content = answer(command, outdir, env, knowledge=kb)
        card = True
    elif context.get("parent_id"):
        content = answer_chat(notifier, command, env or {}, context, kb)
        card = True
    elif route == "knowledge" and is_followup(command):
        turns = memory.load(chat, user)
        if not turns:
            if any(w in command for w in ("日报", "报告", "六日", "六天")):
                content = answer(command, outdir, env, knowledge=kb)
            else:
                content = "你想继续讨论或简化哪份内容？请引用那条消息，或重新说明完整问题。"
        elif not env or env.get("KNOWLEDGE_ENABLED", "").lower() != "true":
            content = "知识问答尚未开启，请联系管理员。"
        else:
            previous = turns[-1]
            payload = json.dumps({"原始资料": previous["source"],
                                  "最近对话": [{"问题": t["question"], "回答": t["answer"]} for t in turns],
                                  "要求": "沿用原资料和数据窗口，按本次追问调整回答。要求简单时最多三句话。不要把之前模型回答当成新增事实依据。"}, ensure_ascii=False)
            content = kb.ask(command, env, report_context=payload)
            if kb.last_context:
                kb.last_context = previous["source"]
                kb.report_snapshot = previous.get("report_snapshot")
        card = True
    elif context and any(w in command for w in ("上一份日报", "上份日报", "上一条日报", "刚才的讨论", "最近聊天")):
        content = answer_chat(notifier, command, env or {}, context, kb)
        card = True
    else:
        content = answer(effective_command, outdir, env, knowledge=kb)
        card = route == "knowledge"
        if route in ("report", "plan") and content.startswith("数据窗口："):
            # 固定查询结果保存为原资料；不在追问时重新读取可能已更新的日报。
            kb.last_context = content
    snapshot = getattr(kb, 'report_snapshot', None)
    display_kind = 'answer'
    if isinstance(snapshot, dict) and route in ('report', 'plan') and not card:
        rendered = report_reply(snapshot, plans_only=route == 'plan')
        if intent == 'data':
            rendered['header']['title']['content'] = '投流助手｜日报数据表格'
        display_kind = 'plan' if route == 'plan' else 'data'
        result = notifier.reply_card(message_id, rendered)
    elif isinstance(snapshot, dict) and card:
        # Analysis and follow-ups show the requested answer, never an appended full report.
        title = '投流助手｜日报总结' if '总结' in command or '简单' in command else '投流助手｜日报解读'
        result = notifier.reply_card(message_id, answer_card(content, title=title))
    else:
        result = notifier.reply_markdown(message_id, content) if card else notifier.reply_text(message_id, content)
    if kb.last_context and isinstance(kb.last_context, str):
        memory.save(chat, user, turns + [{"question": command[:2000], "answer": content[:5000], "source": kb.last_context, "report_snapshot": snapshot if isinstance(snapshot, dict) else None, "display_kind": display_kind}])
    return result


def daily_knowledge_context(report, question):
    """Bound prompt size while allowing every saved plan to be retrieved by name/ID."""
    plans = report.get('plans', [])
    query = tokens(question)
    ranked = sorted(enumerate(plans), key=lambda item: (
        -int(str(item[1].get('plan_id', '')) in question and bool(item[1].get('plan_id'))),
        -len(query & tokens(str(item[1].get('plan_name', '')))), item[0]))
    selected = [p for _, p in ranked[:20]]
    return json.dumps({
        '账户': (report.get('source_api') or {}).get('advertiser_id'),
        '数据窗口': report['audit'], '季节': report.get('season'),
        'ROI口径': report.get('roi_basis', '整体支付ROI=整体成交金额/整体消耗'),
        '整体': report.get('totals', {}), '逐计划': selected,
        '计划覆盖': {'完整日报计划数': len(plans), '本次提供计划数': len(selected),
                   '说明': '优先提供问题中ID或名称匹配计划，其余按日报消耗排序；可按名称或ID继续查询其他计划。'},
        '数据完整性检查': report.get('data_quality', {'note': '旧报告未保存新版完整性检查，请按原采集口径核对'}),
        '口径说明': report.get('notes', []),
        '限制': '这是当前账户最新数据截止日的已保存报告，不保证是今天的数据或群内上一条消息。不能当作实时在投计划列表。预算按快照读取时间解释；SOP示例不是实际预算。单日或窗口合计不能推断跨日走势。后台实测与预测分别标注。'
    }, ensure_ascii=False)


def answer_current(command, outdir, env, kb):
    if not env or env.get('KNOWLEDGE_ENABLED', '').lower() != 'true':
        return '知识问答尚未开启，请联系管理员配置。'
    if not env.get('QC_ADVERTISER_IDS', '').strip():
        return '未配置当前千川账户，无法查询今日小时采集。'
    root = Path(outdir).parent if outdir else BASE
    try:
        payload = hourly.today_context(root, env, command)
    except (OSError, ValueError, sqlite3.Error):
        return '今日小时采集暂时无法读取，请检查采集日志后重试；未使用历史日报代替。'
    if payload is None:
        return '当前账户今日尚无已成功采集的完整小时数据。请保持监听运行并等待采集；北京时间0点后须等首个完整小时结束。未使用昨日日报代替。'
    info = json.loads(payload)
    from . import data_quality
    from . import analysis_gate
    quality = data_quality.current(root, env, stale_minutes=analysis_gate.stale_minutes(root))
    if quality['invalid_records']:
        return '本地数据完整性检查未通过，请重新采集核对；未用异常记录生成分析。'
    info['数据完整性检查'] = quality
    payload = json.dumps(info, ensure_ascii=False)
    kb.report_snapshot = None
    from . import current_brief
    if not current_brief.detailed(command):
        direct = current_brief.direct(command, info)
        if direct is not None:
            kb.last_context = json.dumps([{'编号':1,'文档':info.get('资料类型','今日采集'),'内容':payload}], ensure_ascii=False)
            return direct
        return current_brief.render_generated(kb.ask(command, env, report_context=payload), info)
    generated = analysis_gate.safe_answer(kb.ask(command, env, report_context=payload), info)
    result = data_quality.summary(quality)+'\n'+(info.get('分析限制', '')+'\n' if info.get('分析限制') else '')+generated
    if info.get('资料类型') == '今日半小时采集':
        return '今日累计快照：%s｜账户%s｜采集于%s。\n' % (info['日期'], info['账户'], info['最新采集时间']) + result
    return '今日采集：%s｜账户%s｜已采集%d个完整小时，缺失%d个小时；不含正在进行的小时。\n' % (
        info['日期'], info['账户'], len(info['覆盖小时']), len(info['缺失小时'])) + result


def answer(command, outdir=None, env=None, knowledge=None):
    """只回答已保存的数据，实测和预测分开标注。"""
    command = re.sub(r"\s+", " ", command.strip())
    from . import collection_rates
    if collection_rates.matches(command):
        root = Path(outdir).parent if outdir else BASE
        return '\n'.join(collection_rates.sections(collection_rates.inspect(root, env if env is not None else load_env())))
    if command in ("帮助", "菜单", "?", "？", "help", "Help", "HELP", ""):
        return HELP
    if hourly.is_current(command):
        return answer_current(command, outdir, env, knowledge or Knowledge())
    route = command_route(command)
    if route == "knowledge":
        if not env or env.get("KNOWLEDGE_ENABLED", "").lower() != "true":
            return "知识问答尚未开启，请联系管理员配置。可发送“帮助”查看报告查询指令。"
        kb = knowledge or Knowledge()
        if any(word in command for word in ("日报", "报告", "六日", "六天", "6天", "这几天")):
            season = "summer" if "夏季" in command else "autumn_winter" if "秋冬" in command else None
            report = _latest_report(outdir or BASE / "out", season, account=(env or {}).get("QC_ADVERTISER_IDS"))
            if report is None:
                return "当前账户没有可供总结的模型日报，请先生成报告；未使用其他账户的数据。"
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", command)
            if any(d not in (report["audit"]["start"], report["audit"]["end"]) for d in dates):
                return "当前可用于总结的最新报告窗口为%s；与指定日期不符，请先生成对应日报。" % _header(report)
            context = daily_knowledge_context(report, command)
            question = command
            if '总结' in command and not any(w in command for w in ('详细', '逐计划', '全部计划', '展开', '明细')):
                question += '\n请用3条结论、约150字概括：整体表现、主要贡献、需关注事项。仅保留关键数字，不逐项罗列全部指标或计划，不生成表格。'
            result = kb.ask(question, env, report_context=context)
            if kb.last_context:
                kb.report_snapshot = report
            return "数据窗口：%s。\n" % _header(report) + result
        question = command.removeprefix("问答").strip()
        # Dynamic report context stays separate from the permanent SOP index.
        # Never load another account (or an unscoped legacy report) for ordinary Q&A.
        account = env.get('QC_ADVERTISER_IDS', '').strip()
        report = _latest_report(outdir or BASE / 'out', account=account) if account else None
        if report:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", command)
            if any(d not in (report['audit']['start'], report['audit']['end']) for d in dates):
                return '当前知识问答可用日报窗口为%s；与指定日期不符，请先生成对应日报。' % _header(report)
            result = kb.ask(question, env, report_context=daily_knowledge_context(report, question))
            if kb.last_context:
                kb.report_snapshot = report
            return '数据窗口：%s。\n' % _header(report) + result
        return kb.ask(question, env)
    if route == "plan" and command == "计划":
        return "请使用“计划 名称”或“计划 计划ID”查询，例如：计划 托管。"
    season = "summer" if "夏" in command else "autumn_winter" if "秋" in command or "冬" in command else None
    report = _latest_report(outdir or BASE / "out", season, account=(env or {}).get("QC_ADVERTISER_IDS"))
    if report is None:
        return "当前账户没有找到%s已保存的模型报告。请先生成当前账户的完整日报；未使用其他账户的数据。" % ({"summer": "夏季", "autumn_winter": "秋冬"}.get(season, ""))
    a = report["audit"]
    stamp = "数据窗口：%s。历史报告，不代表当前实时结果。" % _header(report)
    if route == "report":
        if knowledge is not None:
            knowledge.report_snapshot = report
        t = report["totals"]
        return (stamp + "\n后台实测：毛成交 %s元，广告消耗 %s元，整体支付ROI %s。\n"
                "模型预测：退款后净成交 %s元，预计利润 %s元。15%%日消耗参考 %s元；固定成交下保本日消耗参考 %s元。"
                "\n退款率和利润为历史情景推算，最终以实际退款及结算核对。" %
                tuple(_money(t.get(k)) for k in ("gmv", "ad_spend", "payment_roi",
                                                   "predicted_net", "predicted_profit",
                                                   "daily_cap_reference", "daily_breakeven_spend")))
    term = command.removeprefix("计划").strip()
    if not term:
        return HELP
    if len(term) > 30:
        return "计划名称过长。" + HELP
    matches = [p for p in report.get("plans", []) if term in str(p.get("plan_name", "")) or term == str(p.get("plan_id", ""))]
    if not matches:
        return stamp + "\n未找到“%s”的计划。可发送“报告”查看整体结果。" % term
    if knowledge is not None:
        knowledge.report_snapshot = dict(report, plans=matches)
    from .budgets import describe
    lines = [stamp]
    for p in matches[:8]:
        lines.append("%s（%s）：实测毛成交 %s元、消耗 %s元、整体支付ROI %s；预测利润 %s元；15%%日消耗参考 %s元。" %
                     (p.get("plan_name", ""), p.get("plan_id", ""),
                      _money(p.get("gmv")), _money(p.get("ad_spend")),
                      _money(p.get("payment_roi")), _money(p.get("predicted_profit")),
                      _money(p.get("daily_cap_reference"))))
        lines.append(describe(p))
    if len(matches) > 8:
        lines.append("另有%d条匹配计划，请输入更完整的名称。" % (len(matches) - 8))
    lines.append("预测利润基于历史退款假设；不是实际结算利润。")
    return "\n".join(lines)


class Inbox:
    """持久化事件去重；回调只入库，网络回复由后台线程执行。"""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS incoming (message_id TEXT PRIMARY KEY, command TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def set_context(self, message_id, context):
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS message_context (message_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("INSERT OR REPLACE INTO message_context VALUES (?,?)", (message_id, json.dumps(context)))

    def get_context(self, message_id):
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS message_context (message_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            row = db.execute("SELECT payload FROM message_context WHERE message_id=?", (message_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def add(self, message_id, command, context=None):
        with self._db() as db:
            cur = db.execute("INSERT OR IGNORE INTO incoming (message_id,command) VALUES (?,?)", (message_id, command))
            added = cur.rowcount == 1
            if added and context is not None:
                db.execute("CREATE TABLE IF NOT EXISTS message_context (message_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                db.execute("INSERT OR REPLACE INTO message_context VALUES (?,?)", (message_id, json.dumps(context)))
            return added

    def next(self):
        with self._db() as db:
            return db.execute("SELECT message_id,command,attempts FROM incoming WHERE status='pending' AND next_at<=? ORDER BY rowid LIMIT 1", (time.time(),)).fetchone()

    def done(self, message_id):
        with self._db() as db:
            db.execute("UPDATE incoming SET status='sent' WHERE message_id=?", (message_id,))

    def failed(self, message_id, attempts, permanent=False):
        attempts += 1
        with self._db() as db:
            db.execute("UPDATE incoming SET attempts=?,status=?,next_at=? WHERE message_id=?",
                       (attempts, "failed" if permanent or attempts >= 5 else "pending",
                        time.time() + min(60, 2 ** attempts), message_id))


def _extract(event):
    payload = _value(event, "event")
    msg = _value(payload, "message")
    if _value(msg, "chat_type") != "group" or _value(msg, "message_type") != "text":
        return None
    message_id, chat_id = _value(msg, "message_id"), _value(msg, "chat_id")
    if not message_id or not chat_id:
        return None
    try:
        text = json.loads(_value(msg, "content") or "{}").get("text", "")
    except (ValueError, TypeError):
        return None
    # 群里只响应带 @ 占位符的消息；非文本或普通群消息静默忽略。
    if not re.search(r"@_user_\d+", text):
        return None
    command = re.sub(r"@_user_\d+", "", text).strip()
    return message_id, chat_id, command


def run(outdir=None, db_path=None, env=None):
    from .reliability import file_lock, BusyError
    # OS releases this lock on exit/crash; the file itself is not a stale PID lock.
    try:
        with file_lock(BASE / 'data' / 'listener.lock'):
            return _run(outdir, db_path, env)
    except BusyError as exc:
        raise RuntimeError('无法启动监听：同项目已有监听实例，或监听锁不可用。') from exc


def process_one(inbox, notifier, outbox, outdir, env):
    from .delivery import dispatch
    from .reliability import retryable
    item = inbox.next()
    if item is None:
        return False
    message_id, command, attempts = item
    account = env.get('QC_ADVERTISER_IDS')
    request_context = inbox.get_context(message_id) or {}
    if account and request_context.get('advertiser_id') != account:
        inbox.failed(message_id, attempts, permanent=True)
        print('已停止处理账户切换前的历史待办消息 %s，请在当前账户下重新提问。' % message_id, flush=True)
        return True
    try:
        dispatch(notifier, inbox, message_id, command, reply_command, outbox,
                 outdir=outdir, env=env, context=inbox.get_context(message_id))
        inbox.done(message_id)
        print('已回复消息 %s' % message_id, flush=True)
    except Exception as exc:
        permanent = not retryable(exc)
        inbox.failed(message_id, attempts, permanent=permanent)
        # No raw exception or upstream body: it may contain credentials or chat data.
        print('消息处理%s %s（%s）' % ('终止' if permanent or attempts >= 4 else '待重试',
                                    message_id, type(exc).__name__), flush=True)
    return True


def _run(outdir=None, db_path=None, env=None):
    env = env or load_env()
    app_id = (env.get("FEISHU_APP_ID") or "").strip()
    app_secret = (env.get("FEISHU_APP_SECRET") or "").strip()
    allowed = set(feishu_allowed_chat_ids(env) or [(env.get("FEISHU_CHAT_ID") or "").strip()])
    allowed.discard("")
    if not app_id or not app_secret or not allowed:
        raise ValueError("需要在 config/.env 配置 FEISHU_APP_ID、FEISHU_APP_SECRET 和 FEISHU_CHAT_ID（或 FEISHU_ALLOWED_CHAT_IDS）")
    vendor = BASE / "vendor"
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    try:
        import lark_oapi as lark
    except ImportError as exc:
        raise RuntimeError("缺少飞书官方 SDK：在项目目录运行 python -m pip install --target vendor lark-oapi") from exc

    inbox = Inbox(db_path or BASE / "data" / "listener.db")
    notifier = FeishuAppNotifier(app_id, app_secret, next(iter(allowed)))

    def on_message(event):
        parsed = _extract(event)
        if parsed is None:
            return
        message_id, chat_id, command = parsed
        if chat_id in allowed:
            msg = _value(_value(event, "event"), "message")
            request_context = {"chat_id": chat_id, "parent_id": _value(msg, "parent_id"),
                               "create_time": _value(msg, "create_time"),
                               "advertiser_id": env.get("QC_ADVERTISER_IDS"),
                               "sender_id": _value(_value(_value(_value(event, "event"), "sender"), "sender_id"), "open_id")}
            # Make the event and its scope visible atomically to the worker.
            inbox.add(message_id, command, context=request_context)


    from .runtime import ListenerSupervisor
    from .delivery import Outbox
    from . import qc_auth, health
    collector = hourly.Collector(BASE, env)
    from .monitor import Engine
    monitor_engine = Engine(BASE, env, lambda chat: FeishuAppNotifier(app_id, app_secret, chat))
    from .daily_brief import Engine as DailyBriefEngine
    daily_brief_engine = DailyBriefEngine(BASE, env, lambda chat: FeishuAppNotifier(app_id, app_secret, chat))
    print("定时重点日报v21已加载：默认北京时间10点发送昨日完整日。", flush=True)
    outbox = Outbox(BASE / 'data' / 'replies.db')
    supervisor = ListenerSupervisor(
        lambda: process_one(inbox, notifier, outbox, outdir, env),
        lambda: health.run(BASE, env, '授权维护', lambda: qc_auth.maintain(env)))
    health_engine = health.Engine(BASE, env,
        lambda chat: FeishuAppNotifier(app_id, app_secret, chat), {
            '小时采集': lambda: collector.thread, '半小时监测': lambda: monitor_engine.thread,
            '重点日报': lambda: daily_brief_engine.thread, '消息处理': lambda: supervisor.worker,
            '授权维护': lambda: supervisor.supervisor}, worker_tick=lambda: supervisor.last_tick)
    handler = build_event_handler(lark, on_message)
    # SDK 的 INFO 连接日志含临时 WebSocket 凭据，避免打印到控制台或日志文件。
    client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.ERROR)
    print("飞书 @ 消息监听中｜原生字段卡片 v21（10点昨日重点日报）；仅响应已配置的 %d 个群。按 Ctrl+C 停止。" % len(allowed), flush=True)
    client.on_reconnecting = lambda: diagnostics.emit('connection_interrupted')
    client.on_reconnected = lambda: diagnostics.emit('connection_restored')
    try:
        supervisor.start()
        collector.start()
        monitor_engine.start()
        daily_brief_engine.start()
        health_engine.start()
        print("运行健康与数据检查 v23 已启动；进程退出检测须另启系统独立巡检。", flush=True)
        client.start()
    finally:
        health_engine.stop()
        daily_brief_engine.stop()
        monitor_engine.stop()
        collector.stop()
        supervisor.stop()

# health-v23

# analysis-admission-v26

# current-brief-v27
