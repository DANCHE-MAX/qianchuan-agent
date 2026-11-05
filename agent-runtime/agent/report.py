"""输出层：日报、方案、余额提醒三套文案。

注意企业微信 markdown 的能力边界：
  ✅ 加粗、引用、链接、列表、颜色
  ❌ 表格
所以这里全部用「对齐的纯文本行」，不用 markdown 表格。
"""
from datetime import datetime, timedelta


def _f(value, digits=2):
    return "—" if value is None else ("%.*f" % (digits, value))


def _short(name, n=14):
    """计划名可能很长（商品全名），列表里截断，完整名在后台看。"""
    name = (name or "").strip()
    return name if len(name) <= n else name[:n] + "…"

def _money(value):
    return "—" if value is None else "{:,.0f}".format(value)


def aggregate(plans, default_gm=None, breakeven=None):
    spend = sum(p["spend_today"] or 0 for p in plans)
    gmv = sum(p["gmv"] or 0 for p in plans)
    refund = sum(p["refund"] or 0 for p in plans)
    gm_num = sum((p["gmv"] or 0) * (p["gross_margin"] or 0) for p in plans)
    gm = (gm_num / gmv) if gmv > 0 else None
    # 计划上没填毛利率时（千川报表本来就没有这一列），用配置的默认值兜底
    if (not gm or gm <= 0) and default_gm:
        gm = float(default_gm)
    net_gmv = gmv - refund
    return {
        "spend": spend,
        "gmv": gmv,
        "refund": refund,
        "net_gmv": net_gmv,
        "net_roi": (net_gmv / spend) if spend > 0 else None,
        "gross_margin": gm,
        "breakeven": float(breakeven) if breakeven else ((1.0 / gm) if gm and gm > 0 else None),
        "count": len(plans),
    }


def _by_platform(plans, default_gm=None, breakeven=None):
    buckets = {}
    for p in plans:
        buckets.setdefault(p.get("platform") or "未知", []).append(p)
    return {k: aggregate(v, default_gm, breakeven) for k, v in sorted(buckets.items())}


def _split(decisions):
    todo, blocked, watch, unknown, skipped = [], [], [], [], []
    for d in decisions:
        if d["action"] in ("decrease", "pause"):
            (todo if d.get("executable") else blocked).append(d)
        elif d["action"] == "increase":
            blocked.append(d)
        elif d["rule_id"] == "R001":
            unknown.append(d)
        elif d["action"] == "none":
            skipped.append(d)
        else:
            watch.append(d)
    return todo, blocked, watch, unknown, skipped


# ---------------------------------------------------------------- 日报
def daily_markdown(store, cfg, plans, decisions, report_date, prev_date, now):
    default_gm = cfg["breakeven"]["gross_margin_default"]
    be = cfg["breakeven"].get("direct_roi")
    total = aggregate(plans, default_gm, be)
    platforms = _by_platform(plans, default_gm, be)
    todo, blocked, watch, unknown, skipped = _split(decisions)
    hour = plans[0]["snapshot_hour"] if plans else 0

    lines = []
    lines.append("**投流日报 · %s**（基于 %s %02d:00 快照）" % (report_date[5:], report_date, hour))
    lines.append("")

    # ① 一句话
    head = "昨日共 %d 个计划，消耗 %s 元，净ROI %s" % (
        total["count"], _money(total["spend"]), _f(total["net_roi"]))
    if total["breakeven"]:
        gap = (total["net_roi"] or 0) - total["breakeven"]
        head += "，%s保本线 %s（%s %s）" % (
            "高于" if gap >= 0 else "低于", _f(total["breakeven"]),
            "多" if gap >= 0 else "差", _f(abs(gap)))
    lines.append(head + "。")
    if todo:
        lines.append("今天有 **%d 件事必须处理**。" % len(todo))
    else:
        lines.append("今天没有必须处理的事。")
    lines.append("")

    # ② 总览
    lines.append("**昨日总览**")
    lines.append("> 消耗 %s ｜ GMV %s ｜ 实收 %s ｜ 净ROI %s ｜ 保本 %s" % (
        _money(total["spend"]), _money(total["gmv"]), _money(total["net_gmv"]),
        _f(total["net_roi"]), _f(total["breakeven"])))
    # 按配置里期望覆盖的平台逐个列，没数据的显式标「暂无数据」。
    # 静默缺失比可见缺失危险：你以为覆盖了双平台，其实只有一个。
    expected = (cfg.get("data") or {}).get("platforms") or list(platforms.keys())
    for name in expected:
        agg = platforms.get(name)
        if not agg:
            lines.append("> %s：**暂无数据**（未接入）" % name)
            continue
        lines.append("> %s：消耗 %s ｜ GMV %s ｜ 净ROI %s ｜ 保本 %s" % (
            name, _money(agg["spend"]), _money(agg["gmv"]),
            _f(agg["net_roi"]), _f(agg["breakeven"])))
    for name, agg in platforms.items():
        if name not in expected:
            lines.append("> %s：消耗 %s ｜ GMV %s ｜ 净ROI %s ｜ 保本 %s" % (
                name, _money(agg["spend"]), _money(agg["gmv"]),
                _f(agg["net_roi"]), _f(agg["breakeven"])))
    lines.append("")

    # ③ 昨日决策回顾（闭环）
    lines.append("**昨日决策回顾**")
    prev_items = [d for d in store.decisions_on(prev_date)
                  if str(d.get("decision_id", "")).startswith("suggest-")
                  and d.get("action") in ("decrease", "increase", "pause")]
    if not prev_items:
        lines.append("> 暂无历史建议可回顾（系统刚上线，从今天开始积累）")
    else:
        stats = {}
        for d in prev_items:
            stats[d.get("adopted") or "pending"] = stats.get(d.get("adopted") or "pending", 0) + 1
        lines.append("> 昨日给出 %d 条建议：采纳 %d ｜ 修改后采纳 %d ｜ 拒绝 %d ｜ 待处理 %d" % (
            len(prev_items), stats.get("adopted", 0), stats.get("modified", 0),
            stats.get("rejected", 0), stats.get("pending", 0)))
        ignored = [d for d in prev_items if d.get("adopted") == "rejected"]
        if ignored:
            sample = ignored[0]
            lines.append("> 被拒的建议：%s（%s）" % (sample.get("plan_id"), sample.get("finding")))
    lines.append("")

    # ④ 今天必须处理
    lines.append("**今天必须处理**")
    if not todo:
        lines.append("> 无")
    # 昨天建议过、但没处理的计划，今天要特别标出来 —— 这是日报里最有说服力的一句话
    prev_state = {d["plan_id"]: (d.get("adopted") or "pending") for d in prev_items}
    for i, d in enumerate(todo[:3], 1):
        label = {"pause": "暂停计划", "decrease": "降预算", "increase": "加预算"}.get(
            d["action"], d["action"])
        target = ""
        # 暂停不带金额：暂停就是暂停，把它写成「预算改成 0」是两回事
        if d["action"] != "pause" and d.get("target_budget") is not None:
            target = " → %s 元" % _money(d["target_budget"])
        flag = ""
        if prev_state.get(d["plan_id"]) == "rejected":
            flag = "（**昨日已建议，被拒**）"
        elif prev_state.get(d["plan_id"]) == "pending":
            flag = "（昨日已建议，未处理）"
        lines.append("> %d. %s %s：%s%s" % (i, d["plan_id"], _short(d["plan_name"]), d["finding"], flag))
        lines.append(">    动作：%s%s ｜ 验收：%s" % (label, target, d["acceptance"]))
    if len(todo) > 3:
        lines.append("> 其余 %d 条见 16:30 方案" % (len(todo) - 3))
    lines.append("")

    # ⑤ 需要留意（只放预警，不放被风控拦下的预算动作）
    lines.append("**需要留意**")
    notes = [d for d in watch if d["rule_id"] in ("R004", "R006")]
    if not notes:
        lines.append("> 无")
    for d in notes[:3]:
        lines.append("> · %s %s：%s" % (d["plan_id"], _short(d["plan_name"]), d["finding"]))
    if blocked:
        lines.append("> 另有 %d 条预算建议被风控拦下（配额/冷却/加预算关闭），见 16:30 方案"
                     % len(blocked))
    lines.append("")

    # ⑥ 不判断
    lines.append("**数据不足，不判断**")
    # 消耗不到 1 元的计划没有判断价值，只计数不罗列，否则一屏全是噪音
    real_unknown = [d for d in unknown if (d["metrics"]["spend"] or 0) >= 1]
    tiny = len(unknown) - len(real_unknown)
    if not unknown:
        lines.append("> 无")
    for d in real_unknown[:3]:
        lines.append("> · %s %s：%s（拒绝用默认值兜底）" % (
            d["plan_id"], _short(d["plan_name"]), d["finding"]))
    if tiny:
        lines.append("> 另有 %d 个计划消耗不足 1 元，已忽略" % tiny)
    lines.append("")

    # ⑦ 今日提醒（只说今天真要做的事，不写系统说明书）
    lines.append("**今日提醒**")
    lines.append("> · 16:30 会推晚高峰预算方案，看完再决定改不改")
    link = (cfg.get("notify", {}) or {}).get("feedback_link")
    if link and "填你的" not in link:
        lines.append("> · 反馈表：[点此填写](%s)" % link)
    lines.append("")

    # ⑧ 口径（一行，放最后，不占版面）
    lines.append("_净ROI =（GMV−退款）÷消耗 ｜ 保本ROI = 1÷毛利率_")
    return "\n".join(lines)


# ---------------------------------------------------------------- 预算方案
def suggest_markdown(decisions, cfg, date_str, now):
    todo, blocked, watch, unknown, skipped = _split(decisions)
    actionable = [d for d in todo + blocked if d["action"] in ("decrease", "increase", "pause")]

    lines = []
    lines.append("**晚高峰预算方案 · %s %s**" % (date_str[5:], now.strftime("%H:%M")))
    lines.append("")
    lines.append("> 共 %d 个计划，建议调整 **%d 个**。人工确认后请到后台逐个填写。" % (
        len(decisions), len(actionable)))
    lines.append("")

    if not actionable:
        lines.append("**今日无需调整预算。**")
    else:
        lines.append("**建议调整**")
        for i, d in enumerate(actionable[:8], 1):
            target = "—" if d.get("target_budget") is None else _money(d["target_budget"])
            mark = "" if d.get("executable") else "（需人工判断）"
            lines.append("> %d. %s %s" % (i, d["plan_id"], d["plan_name"]))
            lines.append(">    当前 %s → 建议 **%s**（%s）%s" % (
                _money(d.get("current_budget")), target, d["action"], mark))
            lines.append(">    依据：%s" % d["finding"])
            lines.append(">    验收：%s" % d["acceptance"])
        if len(actionable) > 8:
            lines.append("> （其余 %d 条略）" % (len(actionable) - 8))
    lines.append("")

    if blocked:
        lines.append("**被风控闸门拦下**")
        for d in blocked[:3]:
            lines.append("> · %s：%s" % (d["plan_id"], "；".join(d.get("gate_blocks") or [])))
        lines.append("")

    lines.append("**怎么确认**")
    link = (cfg.get("notify", {}) or {}).get("feedback_link")
    lines.append("> 1. 在后台按上面的数字修改预算")
    lines.append("> 2. 打开反馈表勾选「采纳 / 修改后采纳 / 拒绝」")
    if link:
        lines.append("> 反馈表：%s" % link)
    lines.append("> 3. 只有记录下来的建议才能被验证，没记录等于没发生")
    return "\n".join(lines)


# ---------------------------------------------------------------- 余额提醒
def balance_message(item, action, cfg, now):
    level = item["level"]
    acc = item["account"]
    eta = item["eta_hours"]

    deadline = "—"
    if eta is not None:
        left = max(0.0, eta - item["settle_hours"])
        deadline = (now + timedelta(hours=left)).strftime("%m-%d %H:%M")

    if action == "stale":
        text = (
            "[余额·数据过期] %s\n"
            "最后一次更新：%s（%s 小时前）\n"
            "无法判断余额是否够用，请先更新余额表。\n"
            "数据过期时不升级提醒，避免误报。"
        ) % (acc, item["updated_at"], item["data_age_hours"])
        return text, ["投手"]

    if action == "recover":
        text = (
            "[余额·已恢复] %s\n"
            "当前余额 %s 元 ｜ 预计可跑 %s 小时\n"
            "提醒已自动解除，无需处理。"
        ) % (acc, _money(item["balance"]), _f(eta, 1))
        return text, []

    tags = {"urgent": "充值·紧急", "today": "充值·今日", "hint": "充值·提示"}
    tag = "升级·未处理" if action == "escalate" else tags.get(level, "充值")

    text = (
        "[%s] %s\n"
        "余额 %s 元 ｜ 消耗速率 %s 元/小时\n"
        "预计可跑 %s 小时（约 %s 耗尽）\n"
        "动态安全线 %s 元（到账 %s 小时 + 缓冲 %s 小时）\n"
        "建议充值 %s 元（补至上限 %s 元）\n"
    ) % (
        tag, acc, _money(item["balance"]), _f(item["rate"], 0),
        _f(eta, 1), (now + timedelta(hours=eta)).strftime("%m-%d %H:%M") if eta else "—",
        _money(item["floor"]), _f(item["settle_hours"], 1), _f(item["buffer_hours"], 1),
        _money(item["topup"]), _money(item["cap"]),
    )

    if action == "escalate":
        text += "最晚到账 %s。\n" % deadline
        text += "已提醒超过 %d 分钟未处理，请负责人直接跟进。\n" % int(
            cfg["balance"].get("escalate_after_minutes", 60))
        return text, ["老板"]

    if level == "urgent":
        text += "最晚到账 %s，请立即确认。\n" % deadline
    elif level == "today":
        text += "请今日下班前完成充值（财务下班后无法付款）。\n"
    else:
        text += "预计稍后才会耗尽，今日下班前处理即可。\n"
    return text, ["投手"]
