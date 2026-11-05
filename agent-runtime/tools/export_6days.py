"""导出 10-15~10-20 六天的分析数据集，给外部模型（GPT）用。

产出两个文件：
  out/export_6days_plans.csv   计划 × 日 明细（48 行以内，可直接粘给模型）
  out/export_6days_readme.md   口径说明 + 汇总表 + 退货率参照 + 待验证问题
"""
import csv
import glob
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from agent.sources import XlsxPlanSource

BE = 3.3          # 保本 ROI（纯广告消耗口径）
RET_REF = 0.5986  # 2025-10 金额退货率，作为当期参照

OUT_CSV = os.path.join(BASE, "out", "export_6days_plans.csv")
OUT_MD = os.path.join(BASE, "out", "export_6days_readme.md")


def main():
    path = glob.glob(os.path.join(BASE, "example", "*9000000000000099.xlsx"))[0]
    plans = [p for p in XlsxPlanSource(path).fetch()
             if "2026-10-15" <= p["_date"] <= "2026-10-20"]
    plans.sort(key=lambda p: (p["_date"], -(p["spend_today"] or 0)))

    rows = []
    for p in plans:
        spend = p["spend_today"] or 0
        gross = p["gmv"] or 0
        net = gross - (p["refund"] or 0)
        orders = p["orders"] or 0
        if spend <= 0 and gross <= 0:
            continue
        adj_net = gross * (1 - RET_REF)
        rows.append({
            "date": p["_date"],
            "plan_id": p["plan_id"],
            "plan_name": p["plan_name"],
            "spend": round(spend, 2),
            "gmv_gross": round(gross, 2),
            "net_gmv_qc": round(net, 2),
            "orders": orders,
            "refund_qc": round(gross - net, 2),
            "gross_roi": round(gross / spend, 2) if spend else "",
            "net_roi_qc": round(net / spend, 2) if spend else "",
            "aov_net": round(net / orders, 0) if orders else "",
            "cost_per_order": round(spend / orders, 2) if orders else "",
            "net_roi_adj": round(adj_net / spend, 2) if spend else "",
            "pnl_adj": round(adj_net - BE * spend, 0),
            # 消耗太小的时候比率会爆炸（花 1 元卖 299 → ROI 262），
            # 这类行只能看"花了多少"，不能看比率。分析比率时请过滤掉。
            "tiny": "tiny" if spend < 50 else "",
        })

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # 汇总
    def agg(rs):
        s = sum(r["spend"] for r in rs)
        g = sum(r["gmv_gross"] for r in rs)
        n = sum(r["net_gmv_qc"] for r in rs)
        o = sum(r["orders"] for r in rs)
        adj = g * (1 - RET_REF)
        return s, g, n, o, adj

    S, G, N, O, ADJ = agg(rows)

    by_day, by_plan = {}, {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
        by_plan.setdefault(r["plan_id"], []).append(r)

    md = []
    md.append("# 千川投放数据集 · 2026-10-15 ~ 10-20（6 个完整日）\n")
    md.append("## 一、口径（必须先读，否则会算错）\n")
    md.append("| 项 | 说明 |")
    md.append("|---|---|")
    md.append("| 数据源 | 千川「乘方推广-数据」计划维度导出，已剔除「日期=全部」「小时区间≠全部」的汇总行 |")
    md.append("| 消耗 | 整体消耗（纯广告费） |")
    md.append("| 毛成交 | 整体成交金额（未扣退款） |")
    md.append("| 净成交(千川) | 净成交金额。**只扣了导出时刻已发生的退款，是下界，不是终值** |")
    md.append("| 保本 ROI | **3.3**，纯广告消耗口径，不含佣金（实测佣金为 0）和货品成本 |")
    md.append("| 退货率参照 | **59.86%**（2025-10 全店金额退货率，已清洗）。这是**参照值，不是当期实测** |")
    md.append("| 净ROI(修正) | `毛成交 × (1 − 59.86%) ÷ 消耗` |")
    md.append("| 盈亏(修正) | `毛成交 × (1 − 59.86%) − 3.3 × 消耗`（元） |")
    md.append("")
    md.append("> ⚠️ 本数据集**不含 2026-10-15~20 的订单明细**。订单数据只有 2025-10~12 和 2026-05~09。")
    md.append("> 所以 59.86% 是**去年同月**的退货率，用于修正，不等于这六天的真实退款。\n")
    md.append("> ⚠️ CSV 里 `tiny` 列标了 `tiny` 的行，是消耗 < 50 元的计划。")
    md.append("> **这些行的比率类指标（ROI、客单价、单笔花费）会爆炸失真**（花 1 元卖 299 → ROI 262）。")
    md.append("> 分析比率时请先用 `tiny == \"\"` 过滤，只保留正常消耗的行；但算总亏损时要保留它们。\n")

    md.append("## 二、六天合计\n")
    md.append("| 指标 | 千川口径 | 修正后 |")
    md.append("|---|---|---|")
    md.append("| 消耗 | %.2f | %.2f |" % (S, S))
    md.append("| 毛成交 | %.2f | %.2f |" % (G, G))
    md.append("| 净成交 | %.2f | %.2f |" % (N, ADJ))
    md.append("| 单量 | %d | %d |" % (O, O))
    md.append("| 净ROI | %.2f | %.2f |" % (N / S, ADJ / S))
    md.append("| 保本 | %.2f | %.2f |" % (BE, BE))
    md.append("| 盈亏(元) | %.0f | %.0f |" % (N - BE * S, ADJ - BE * S))
    md.append("")

    md.append("## 三、按天\n")
    md.append("| 日期 | 消耗 | 毛成交 | 单量 | 毛ROI | 净ROI(千川) | 净ROI(修正) | 盈亏(修正) |")
    md.append("|---|---|---|---|---|---|---|---|")
    for d in sorted(by_day):
        s, g, n, o, adj = agg(by_day[d])
        md.append("| %s | %.0f | %.0f | %d | %.2f | %.2f | %.2f | %+.0f |"
                  % (d, s, g, o, g / s, n / s, adj / s, adj - BE * s))
    md.append("")

    md.append("## 四、按计划（六天累计）\n")
    md.append("| 计划 | 消耗 | 占比 | 毛成交 | 单量 | 净ROI(千川) | 净ROI(修正) | 客单价 | 单笔花费 |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for pid, rs in sorted(by_plan.items(), key=lambda kv: -sum(r["spend"] for r in kv[1])):
        s, g, n, o, adj = agg(rs)
        md.append("| %s | %.0f | %.0f%% | %.0f | %d | %.2f | %.2f | %.0f | %.2f |"
                  % (rs[0]["plan_name"][:16], s, s / S * 100, g, o, n / s,
                     adj / s, n / o if o else 0, s / o if o else 0))
    md.append("")

    md.append("## 五、退货率参照（已清洗，金额口径）\n")
    md.append("| 月份 | 退货率 | 来源 |")
    md.append("|---|---|---|")
    for m, r, src in [("2025-10", "59.86%", "秋冬"), ("2025-11", "62.58%", "秋冬"),
                      ("2025-12", "65.13%", "秋冬"), ("2026-05", "52.90%", "夏季"),
                      ("2026-06", "57.60%", "夏季"), ("2026-07", "53.35%", "夏季"),
                      ("2026-08", "57.54%", "夏季"), ("2026-09", "61.60%", "夏季")]:
        md.append("| %s | %s | %s |" % (m, r, src))
    md.append("")
    md.append("**季节整体**：夏季 57.55%（77.7 万单）｜ 秋冬 62.33%（39.0 万单）\n")
    md.append("**规律**：随时间上升，5 月 52.9% → 12 月 65.1%，跨度 12 个百分点。\n")

    md.append("## 六、已知的业务事实（可直接引用）\n")
    md.append("1. **未发货订单 100% 全额退款**：秋冬 108,543 单 / 2,380 万（占有效金额 30.0%）；夏季 168,562 单 / 2,400 万（23.4%）。")
    md.append("2. **发货越快退货率越高**：0-1 天 47.3%，8-15 天 40.7%（两个季节一致）。")
    md.append("3. **预售天数中位数 0.5 天**，P90 6 天。")
    md.append("4. 计划结构：2 条主力（托管走量、手动走高客单）+ 1 条小测试 + 5 条空转（6 天合计只花 82 元）。")
    md.append("5. 托管组 ROI 5.67 / 客单价 189；手动组 ROI 5.83 / 客单价 370 —— **ROI 接近，客单价差一倍，是互补不是替代**。\n")

    md.append("## 七、希望模型回答的问题\n")
    md.append("1. 用 59.86% 修正后，这六天到底赚还是亏？哪一天最差？")
    md.append("2. 按计划看，谁在拖累整体？降预算还是暂停？")
    md.append("3. 消耗隔天高低交替（1,864 / 963 / 1,883 / 771 / 929 / 1,958），可能的原因是什么？")
    md.append("4. 如果只能保留 2 条计划，留哪两条？依据是什么？")
    md.append("5. 还有哪些指标值得从这份数据里挖，但当前没算？\n")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print("已写出：%s（%d 行）" % (os.path.relpath(OUT_CSV, BASE), len(rows)))
    print("已写出：%s（%d 行）" % (os.path.relpath(OUT_MD, BASE), len(md)))
    print()
    print("六天合计：消耗 %.0f ｜ 毛成交 %.0f ｜ 单量 %d ｜ 净ROI %.2f → 修正 %.2f ｜ 盈亏 %+.0f"
          % (S, G, O, N / S, ADJ / S, ADJ - BE * S))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
