"""按季节算退货率（夏 / 秋冬）。严格按以下口径：

无效订单（剔除）：
  A. 用户未支付导致的订单关闭（超时未支付、余额不足等）
  B. 商家/平台侧关闭（达人取消、系统自动取消、赠品单取消）
  C. 已支付但被关闭的订单（有取消原因）

有效订单：
  支付完成时间非空 且 无取消原因

退货：
  售后状态 ∈ {退款成功, 已全额退款}
  —— 包含「未发货就点仅退款」的订单（这批单独统计，确认已计入）

用法：python tools/season_return_rate.py
产出：data/exports/season_return_rate.csv
"""
import csv
import os
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORTS = os.path.join(BASE, "data", "exports")
OUT = os.path.join(EXPORTS, "season_return_rate.csv")

REFUND_STATUS = ("退款成功", "已全额退款")
SEASONS = [("夏季", "summer", "2026-05-01", "2026-09-30"),
           ("秋冬", "winter", "2025-10-01", "2025-12-31")]


def classify_cancel(reason, has_pay):
    """把无效订单归到三类里。"""
    r = (reason or "").strip()
    if not has_pay and not r:
        return "A_未完成支付"
    if "超时" in r or "余额不足" in r or "支付方式" in r:
        return "A_用户未支付"
    if "达人取消" in r or "系统" in r or "赠品" in r:
        return "B_商家或平台关闭"
    if r.startswith("你已取消"):
        return "B_用户主动取消" if not has_pay else "C_已支付后被关闭"
    return "C_已支付后被关闭" if has_pay else "A_未完成支付"


def run_season(source, dmin, dmax):
    f = open(source, encoding="utf-8-sig", newline="")
    r = csv.reader(f)
    head = [h.strip() for h in next(r)]
    ix = {h: i for i, h in enumerate(head)}

    i_pay, i_cancel = ix["支付完成时间"], ix["取消原因"]
    i_amt, i_after = ix["订单应付金额"], ix["售后状态"]
    i_ship, i_time = ix["发货时间"], ix["订单提交时间"]

    stat = Counter()
    total_n = 0
    valid_n = 0
    valid_amt = 0.0
    ref_n = 0
    ref_amt = 0.0
    only_refund_n = 0      # 未发货就退款的单量
    only_refund_amt = 0.0  # 未发货就退款的金额

    for row in r:
        if len(row) <= max(i_pay, i_cancel, i_amt, i_after, i_ship):
            continue
        day = row[i_time].strip()[:10]
        if not (dmin <= day <= dmax):
            continue
        total_n += 1

        pay = row[i_pay].strip()
        cancel = row[i_cancel].strip()
        if not pay or cancel:
            stat[classify_cancel(cancel, bool(pay))] += 1
            continue

        valid_n += 1
        try:
            amt = float(row[i_amt].strip().replace(",", "") or 0)
        except ValueError:
            amt = 0.0
        valid_amt += amt

        if row[i_after].strip() in REFUND_STATUS:
            ref_n += 1
            ref_amt += amt
            if not row[i_ship].strip():      # 没发货就退款 = 仅退款
                only_refund_n += 1
                only_refund_amt += amt
    f.close()

    return {
        "total": total_n, "invalid": stat, "valid": valid_n,
        "valid_amt": valid_amt, "ref_n": ref_n, "ref_amt": ref_amt,
        "only_refund_n": only_refund_n, "only_refund_amt": only_refund_amt,
    }


def main():
    results = []
    for name, label, dmin, dmax in SEASONS:
        src = os.path.join(EXPORTS, "orders_%s.csv" % label)
        if not os.path.exists(src):
            print("缺少 %s" % src)
            continue
        s = run_season(src, dmin, dmax)
        s["name"] = name
        s["range"] = "%s ~ %s" % (dmin, dmax)
        results.append(s)

        inv = sum(s["invalid"].values())
        print("=== %s（%s）===" % (name, s["range"]))
        print("总行数        %10d" % s["total"])
        print("无效订单      %10d  （%.2f%%）" % (inv, inv / s["total"] * 100))
        for k in sorted(s["invalid"]):
            print("   %-18s %8d" % (k, s["invalid"][k]))
        print("有效订单      %10d  （%.2f%%）" % (s["valid"], s["valid"] / s["total"] * 100))
        print("有效金额      %14.2f" % s["valid_amt"])
        print("退货单量      %10d  → 单量退货率 %.2f%%"
              % (s["ref_n"], s["ref_n"] / s["valid"] * 100))
        print("退货金额      %14.2f  → 金额退货率 %.2f%%"
              % (s["ref_amt"], s["ref_amt"] / s["valid_amt"] * 100))
        print("  其中仅退款  %10d（%.1f%% 的退货单）金额 %.2f"
              % (s["only_refund_n"], s["only_refund_n"] / s["ref_n"] * 100,
                 s["only_refund_amt"]))
        print()

    if results:
        with open(OUT, "w", encoding="utf-8-sig", newline="") as g:
            w = csv.writer(g)
            w.writerow(["季节", "期间", "总行数", "无效订单", "有效订单",
                        "有效金额", "退货单量", "退货金额",
                        "单量退货率", "金额退货率", "仅退款单量", "仅退款金额"])
            for s in results:
                w.writerow([s["name"], s["range"], s["total"],
                            sum(s["invalid"].values()), s["valid"],
                            round(s["valid_amt"], 2), s["ref_n"], round(s["ref_amt"], 2),
                            round(s["ref_n"] / s["valid"], 4),
                            round(s["ref_amt"] / s["valid_amt"], 4),
                            s["only_refund_n"], round(s["only_refund_amt"], 2)])
        print("已写出：%s" % os.path.relpath(OUT, BASE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
