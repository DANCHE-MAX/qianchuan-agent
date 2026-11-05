"""余额预警的纯计算层：速率 → ETA → 动态安全线 → 分档。

这里不发消息、不写数据库，只返回一个结构化 dict，方便单测。
消息长什么样在 report.py，状态记忆在 store.py。
"""
from datetime import datetime

NORMAL = "normal"
HINT = "hint"        # ETA < 24h，提示但不急
TODAY = "today"      # 低于动态安全线，今天必须处理
URGENT = "urgent"    # ETA <= 到账时间 + 缓冲，马上会断
STALE = "stale"      # 余额数据过期，无法判断

LEVEL_ORDER = {NORMAL: 0, HINT: 1, TODAY: 2, URGENT: 3, STALE: 1}


def compute_rate(hourly, fallback_hour):
    """算消耗速率（元/小时）。

    优先用窗口速率：拿最近两个小时的累计消耗差 / 小时差。
    只有一次快照时，退化成「今日累计 ÷ 已过小时数」，并标记来源，
    这样报表里能一眼看出这个速率是估算的还是真实的。
    """
    rows = [r for r in hourly if r.get("s") is not None]
    if len(rows) >= 2:
        latest = rows[0]
        earliest = rows[-1]
        gap = latest["h"] - earliest["h"]
        delta = latest["s"] - earliest["s"]
        # 跨天或数据回滚时 delta 会 <= 0，退回估算
        if gap > 0 and delta > 0:
            return round(delta / gap, 2), "window"
    if rows:
        spend = rows[0]["s"] or 0
        if fallback_hour > 0 and spend > 0:
            return round(spend / fallback_hour, 2), "avg_today"
    return 0.0, "no_data"


def evaluate(account, balance, updated_at, rate, cfg, now=None):
    """返回该账户此刻的结构化状态。"""
    now = now or datetime.now()
    b = cfg["balance"]
    override = (b.get("accounts") or {}).get(account) or {}

    cap = float(override.get("cap", b["cap"]))
    settle = float(override.get("settle_hours", b["settle_hours"]))
    buffer_h = float(override.get("buffer_hours", b["buffer_hours"]))
    factor = settle + buffer_h

    floor = round(rate * factor, 2)
    eta = round(balance / rate, 2) if rate > 0 else None

    age_hours = _age_hours(updated_at, now)
    stale_hours = float(b.get("stale_hours", 4))

    if age_hours is None or age_hours > stale_hours:
        level = STALE
    elif eta is None:
        level = HINT
    elif eta <= factor:
        level = URGENT
    elif balance < floor:
        level = TODAY
    elif eta < 24:
        level = HINT
    else:
        level = NORMAL

    # 补满到上限。不要用「覆盖 N 天」去算，因为那样会建议充得比上限还多，
    # 相当于自己把风险敞口放大。上限太低是配置问题，应该在报告里暴露，不能靠充值掩盖。
    topup = max(0.0, round(cap - balance, 2))

    return {
        "account": account,
        "balance": round(balance, 2),
        "rate": rate,
        "eta_hours": eta,
        "floor": floor,
        "cap": cap,
        "topup": topup,
        "settle_hours": settle,
        "buffer_hours": buffer_h,
        "data_age_hours": None if age_hours is None else round(age_hours, 1),
        "level": level,
        "updated_at": updated_at,
    }


def _age_hours(updated_at, now):
    if not updated_at:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return (now - datetime.strptime(updated_at, fmt)).total_seconds() / 3600.0
        except ValueError:
            continue
    return None


def decide_action(current_level, previous, elapsed_minutes=0, escalate_after=60):
    """状态机：决定这次该发什么。

    previous 是数据库里上次记录（dict 或 None）。
    返回动作：silent / warn / escalate / recover / stale
    """
    prev_level = (previous or {}).get("level")
    escalated = bool((previous or {}).get("escalated"))

    if current_level == STALE:
        return "stale" if prev_level != STALE else "silent"

    if current_level == NORMAL:
        if prev_level in (URGENT, TODAY, HINT):
            return "recover"
        return "silent"

    # 本次仍是告警：第一次出现，或从正常掉下来
    if prev_level is None or prev_level in (NORMAL, STALE):
        return "warn"
    if prev_level == HINT and current_level == TODAY:
        return "warn"
    # 同档位持续，且已经等够升级时长
    if current_level == URGENT and not escalated and elapsed_minutes >= escalate_after:
        return "escalate"
    return "silent"
