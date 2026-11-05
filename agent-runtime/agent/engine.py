"""规则引擎：算指标 → 判规则 → 过风控闸门。

三条设计原则，改代码时别破坏：
1. 阈值全部来自 config/rules.json，代码里不出现魔数。
2. 数据不足时标 not_evaluable，绝不用默认值兜底（用默认值兜底＝用假数据做真决策）。
3. 建议与执行分离：本 demo 只出建议，所有决策 can_execute=false。
"""
import json
import os
from datetime import datetime

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def load_history(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("actions", [])


def compute_metrics(plan, cfg):
    """从原始快照算出判断需要的指标。缺数据的指标给 None，不给 0。

    注意：千川乘方导出的报表没有「日预算」「曝光」「点击」，
    这些字段会是 None，对应的判断（R004 消耗进度、R006 素材疲劳）自动跳过，
    而不是拿 0 去算 —— 用 0 算出来的结论是错的，而且看不出来。
    """
    spend = plan.get("spend_today") or 0.0
    gmv = plan.get("gmv") or 0.0
    refund = plan.get("refund") or 0.0
    budget = plan.get("daily_budget") or 0.0
    clicks = plan.get("clicks") or 0
    impressions = plan.get("impressions") or 0
    orders = plan.get("orders") or 0
    gm = plan.get("gross_margin") or cfg["breakeven"]["gross_margin_default"]
    # 保本 ROI 优先用直接配置的那个数：商家往往知道「ROI 低于 3.3 就不赚钱」，
    # 但未必能准确说出毛利率。有 direct_roi 就不再用 1/毛利率 反算。
    be = cfg["breakeven"].get("direct_roi")
    breakeven = float(be) if be else ((1.0 / gm) if gm > 0 else None)

    net_gmv = gmv - refund
    m = {
        "spend": spend,
        "budget": budget,
        "impressions": impressions or None,
        "clicks": clicks or None,
        "orders": orders,
        "gmv": gmv,
        "net_gmv": net_gmv,
        "gross_margin": gm,
        "breakeven_roi": breakeven,
        "roi": (gmv / spend) if spend > 0 else None,
        "net_roi": (net_gmv / spend) if spend > 0 else None,
        "pace": (spend / budget) if budget > 0 else None,
        "ctr": (clicks / impressions) if impressions > 0 else None,
        "cvr": (orders / clicks) if clicks > 0 else None,
        "margin_assumed": not plan.get("gross_margin"),
    }
    return m


def _dec(rule_id, severity, finding, judgment, action, target_budget,
         delta_pct, window_hours, acceptance):
    return {
        "rule_id": rule_id,
        "severity": severity,
        "finding": finding,
        "judgment": judgment,
        "action": action,
        "target_budget": target_budget,
        "delta_pct": delta_pct,
        "window_hours": window_hours,
        "acceptance": acceptance,
    }


def diagnose(plan, m, cfg):
    """按优先级判一条，返回单一主治结论。优先级即干预顺序。"""
    r = cfg["rules"]
    w = cfg["windows"]
    be = m["breakeven_roi"]

    if plan["status"] != "active":
        return _dec("R-SKIP", "info", "未在投放", "已暂停，不参与诊断", "none", None, 0,
                    w["long_watch_hours"], "无需验收")

    # R001 数据不足：先判"能不能判"，再判"该不该动"
    # 有曝光就按曝光判；报表里没曝光（乘方导出）时，退化用消耗门槛判断
    if m["impressions"] is not None:
        if m["impressions"] < r["min_impressions"]:
            return _dec(
                "R001", "info",
                f"曝光 {m['impressions']} < {r['min_impressions']}",
                "数据积累不足，任何结论都不可靠",
                "observe", None, 0, w["long_watch_hours"],
                f"等曝光达到 {r['min_impressions']} 再评估",
            )
    elif m["spend"] < r["min_spend_for_judgement"]:
        return _dec(
            "R001", "info",
            f"消耗 {m['spend']:.0f} 元，样本太小",
            "钱花得太少，看不出这个计划真实水平",
            "observe", None, 0, w["long_watch_hours"],
            f"等消耗超过 {r['min_spend_for_judgement']} 元再评估",
        )

    # R002 零成交高消耗
    if m["orders"] == 0 and m["spend"] >= r["zero_order_spend"]:
        return _dec(
            "R002", "high",
            f"耗 {m['spend']:.0f} 元，0 成交",
            "已过观察门槛仍无转化，继续投是纯消耗",
            "pause", 0, -100, w["long_watch_hours"],
            "24h 后复查是否恢复成交；无成交维持暂停",
        )

    # R003 净 ROI 跌破保本线
    if m["spend"] >= r["min_spend_for_judgement"] and m["net_roi"] is not None \
            and m["net_roi"] < be * r["stop_ratio"]:
        sev = "high" if m["net_roi"] < be * 0.5 else "medium"
        target = round(m["budget"] * (1 - r["max_decrease_pct"] / 100.0), 2)
        # 验收要可达到：降预算的目的是「止血」，不是「2 小时内做到保本」。
        # 所以看两件事：钱有没有降下来、ROI 有没有回到警戒线以上。
        return _dec(
            "R003", sev,
            f"净ROI {m['net_roi']:.2f} < 保本 {be:.2f}×{r['stop_ratio']}",
            "扣退款后已在亏钱，且样本足够（非数据问题）",
            "decrease", target, -r["max_decrease_pct"], w["default_watch_hours"],
            f"{w['default_watch_hours']}h 后复查：消耗 ≤ {target:.0f} 且净ROI 回到 "
            f"{be * r['stop_ratio']:.2f} 以上（保本×{r['stop_ratio']}）",
        )

    # R005 可放量
    if m["net_roi"] is not None and be is not None \
            and m["net_roi"] >= be * r["scale_ratio"] \
            and (m["pace"] or 0) >= 0.9 and m["orders"] >= r["min_orders_for_scale"]:
        target = round(m["budget"] * (1 + r["max_increase_pct"] / 100.0), 2)
        return _dec(
            "R005", "medium",
            f"净ROI {m['net_roi']:.2f} ≥ 保本×{r['scale_ratio']}，预算已用 {m['pace']*100:.0f}%",
            "利润空间和成交量都支持放量，卡在预算上限",
            "increase", target, r["max_increase_pct"], w["default_watch_hours"],
            f"{w['default_watch_hours']}h 后复查：净ROI 未跌破 {be:.2f}",
        )

    # R004 预算消耗过快（时间未到，钱要先花完）
    if m["pace"] is not None and m["pace"] >= r["pace_ratio"] and plan["snapshot_hour"] < r["pace_hour"]:
        return _dec(
            "R004", "medium",
            f"{plan['snapshot_hour']}:00 已耗 {m['pace']*100:.0f}% 预算",
            "按此速度今天会提前停投，晚间高峰无预算承接",
            "adjust_pace", m["budget"], 0, w["long_watch_hours"],
            f"{r['pace_hour']}:00 复查：预算是否在高峰前耗尽",
        )

    # R006 素材疲劳
    if m["ctr"] is not None and m["ctr"] < r["ctr_floor"] and m["spend"] >= r["min_spend_for_judgement"]:
        return _dec(
            "R006", "low",
            f"CTR {m['ctr']*100:.2f}% < {r['ctr_floor']*100:.1f}%",
            "人群或素材吸引力不足，不是出价问题",
            "refresh_creative", None, 0, w["long_watch_hours"],
            f"新素材上线 24h 后对比 CTR ≥ {r['ctr_floor']*100:.1f}%",
        )

    return _dec(
        "R000", "info",
        f"净ROI {m['net_roi']:.2f}，消耗进度 {(m['pace'] or 0)*100:.0f}%",
        "各项指标在阈值内，无需干预", "observe", None, 0, w["long_watch_hours"], "无需验收",
    )


def _minutes_between(now, ts_str):
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return 10 ** 9
    gap = (now - ts).total_seconds() / 60.0
    # 时间戳在未来 = 数据有问题。当成「没有冷却」而不是「无限冷却」，
    # 否则一条脏数据会把整个账户锁死一整天。
    if gap < 0:
        return 10 ** 9
    return gap


def apply_gates(decision, plan, usage, cfg, now):
    """风控闸门。返回 (是否可执行, 拦截原因列表)。

    这是本 demo 唯一"像生产系统"的地方，也是自研投流 Agent 真正的工作量所在：
    规则好写，闸门难写。
    """
    blocks = []
    action = decision["action"]
    if action not in ("decrease", "increase", "pause"):
        decision["gate_blocks"] = ["非预算动作，不需要执行闸门"]
        decision["executable"] = False
        return decision

    g = cfg["gates"]
    account = plan["account"]

    if action == "increase" and not g["allow_increase"]:
        blocks.append("G-INCREASE-OFF：加预算默认关闭，只出建议给人工判断")

    used = usage.get(account, [])
    if len(used) >= g["max_actions_per_account_per_day"]:
        blocks.append(
            f"G-DAILY-QUOTA：该账户今日已执行 {len(used)}/{g['max_actions_per_account_per_day']} 次"
        )
    elif used:
        last = max(u["ts"] for u in used)
        gap = _minutes_between(now, last)
        if gap < g["cooldown_minutes"]:
            if gap < 1:
                blocks.append("G-COOLDOWN：本批次已调整同账户其他计划，避免连环操作")
            else:
                blocks.append(
                    f"G-COOLDOWN：距上次动作 {int(gap)} 分钟 < {g['cooldown_minutes']} 分钟"
                )

    decision["gate_blocks"] = blocks
    decision["executable"] = len(blocks) == 0
    if decision["executable"]:
        usage.setdefault(account, []).append(
            {"account": account, "plan_id": plan["plan_id"],
             "action": action, "ts": now.isoformat(timespec="seconds"),
             "delta_pct": decision["delta_pct"]}
        )
    return decision


def run(plans, cfg, history, now=None):
    """主流程：逐计划诊断 → 按严重度排序 → 按顺序分配执行配额。"""
    now = now or datetime.now()
    usage = {}
    today = now.strftime("%Y-%m-%d")
    for a in history:
        # 配额是「每天」的，昨天的动作不该占今天的额度
        if str(a.get("ts", ""))[:10] != today:
            continue
        usage.setdefault(a["account"], []).append(a)

    results = []
    for p in plans:
        m = compute_metrics(p, cfg)
        d = diagnose(p, m, cfg)
        d.update({
            "platform": p["platform"],
            "account": p["account"],
            "plan_id": p["plan_id"],
            "plan_name": p["plan_name"],
            "campaign_type": p["campaign_type"],
            "snapshot_hour": p["snapshot_hour"],
            "current_budget": p["daily_budget"],
            "metrics": m,
            # 无论 demo 里闸门是否放行，都不允许真的写平台
            "execution": {"mode": "dry_run", "can_execute": False,
                          "note": "只读 demo：不连平台写接口，需人工在后台操作"},
        })
        results.append(d)

    # 严重度优先，同级按净 ROI 从差到好 —— 配额有限时先救最亏的
    results.sort(key=lambda d: (
        SEVERITY_ORDER.get(d["severity"], 9),
        d["metrics"]["net_roi"] if d["metrics"]["net_roi"] is not None else float("inf"),
    ))

    for i, d in enumerate(results, 1):
        plan = {"account": d["account"], "plan_id": d["plan_id"]}
        apply_gates(d, plan, usage, cfg, now)
        d["decision_id"] = "D-%s-%03d" % (now.strftime("%Y%m%d"), i)
        d["ts"] = now.isoformat(timespec="seconds")
    return results
