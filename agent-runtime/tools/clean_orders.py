"""订单数据清洗 + 退货率重算。不修改源文件，只产出聚合结果。

用法：python tools/clean_orders.py summer    读 orders_summer.csv
      python tools/clean_orders.py winter    读 orders_winter.csv

清洗规则：
  有效订单 = 有支付完成时间 且 无取消原因
  无效订单 = 未付款（支付完成时间为空） 或 有取消原因
  退货     = 售后状态 ∈ {退款成功, 已全额退款}（换货/售后关闭 不算）

为什么不用「订单状态=已关闭」筛：已关闭占 58%，但取消原因只覆盖 6.7%，
说明绝大多数「已关闭」是退款完成后的状态，按状态筛会把退款单也删掉。

新增字段：预售天数 = 发货时间 − 支付完成时间
"""
import csv
import os
import sys
from collections import Counter
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORTS = os.path.join(BASE, "data", "exports")

REFUND_STATUS = ("退款成功", "已全额退款")
PRESALE_BUCKETS = [
    ("0-1天", 0, 1), ("2-3天", 2, 3), ("4-7天", 4, 7),
    ("8-15天", 8, 15), ("16-29天", 16, 29), ("30天以上", 30, 10 ** 8),
]


def parse_dt(s):
    """兼容三种格式：完整时间戳、到分钟、只有日期。

    之前只认完整时间戳，导致「2025-10-15」这种只有日期的发货时间
    解析失败，被误归进「未发货」。实测秋冬数据里有 24k 单是这种格式。
    """
    s = (s or "").strip()
    if not s:
        return None
    for fmt, ln in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        if len(s) >= ln:
            try:
                return datetime.strptime(s[:ln], fmt)
            except ValueError:
                continue
    return None


def bucket_of(days):
    """按预售天数分档。

    两个坑都踩过：
      1. days=None 以前和「未发货」共用一个桶，把 30 天以上的订单误标成未发货；
      2. 区间边界用整数比较，但天数带小数 —— 15.5 天既不在 8-15 也不在 16-29，
         会整批落进「无法计算」。所以先向下取整。
    """
    if days is None:
        return "无法计算"
    d = int(days)   # 向下取整，消除 15.5 这类落在区间缝隙里的值
    for name, lo, hi in PRESALE_BUCKETS:
        if lo <= d <= hi:
            return name
    return "无法计算"


def main():
    label = (sys.argv[1] if len(sys.argv) > 1 else "winter").strip().lower()
    src = os.path.join(EXPORTS, "orders_%s.csv" % label)
    if not os.path.exists(src):
        print("找不到 %s" % src)
        return 1
    print("源文件：%s（%.0f MB，不改动）" % (os.path.basename(src),
                                       os.path.getsize(src) / 1024 / 1024))

    f = open(src, encoding="utf-8-sig", newline="")
    r = csv.reader(f)
    head = [h.strip() for h in next(r)]
    ix = {h: i for i, h in enumerate(head)}

    i_amt, i_pay = ix["订单应付金额"], ix["支付完成时间"]
    i_ship, i_cancel = ix["发货时间"], ix["取消原因"]
    i_after, i_time = ix["售后状态"], ix["订单提交时间"]
    i_pid, i_name = ix["商品ID"], ix["选购商品"]

    total = 0
    invalid = Counter()
    mon, prod, presale = {}, {}, {}
    days_list = []
    dmin, dmax = "9999", ""

    for row in r:
        if len(row) <= max(i_amt, i_pay, i_cancel, i_after):
            continue
        total += 1
        cancel, pay = row[i_cancel].strip(), row[i_pay].strip()
        if not pay:
            invalid["未付款"] += 1
            continue
        if cancel:
            invalid["超时未支付" if "超时" in cancel else "取消订单"] += 1
            continue

        try:
            amt = float(row[i_amt].strip().replace(",", "") or 0)
        except ValueError:
            amt = 0.0
        refunded = 1 if row[i_after].strip() in REFUND_STATUS else 0
        ramt = amt if refunded else 0.0

        date = row[i_time].strip()[:10]
        ym = date[:7]
        if date:
            dmin = min(dmin, date)
            dmax = max(dmax, date)

        m = mon.setdefault(ym, [0, 0.0, 0, 0.0])
        m[0] += 1; m[1] += amt; m[2] += refunded; m[3] += ramt

        pid = row[i_pid].strip()
        p = prod.setdefault(pid, [0, 0.0, 0, 0.0, row[i_name].strip()[:30]])
        p[0] += 1; p[1] += amt; p[2] += refunded; p[3] += ramt

        ship, paydt = parse_dt(row[i_ship]), parse_dt(pay)
        days = None
        if ship and paydt:
            days = max(0.0, (ship - paydt).total_seconds() / 86400.0)
            days_list.append(days)
        # 「未发货」和「算不出来」必须分开：前者是业务事实，后者是数据问题
        bname = "未发货" if not row[i_ship].strip() else bucket_of(days)
        b = presale.setdefault(bname, [0, 0.0, 0, 0.0])
        b[0] += 1; b[1] += amt; b[2] += refunded; b[3] += ramt

    f.close()

    T = [0, 0.0, 0, 0.0]
    for v in mon.values():
        for i in range(4):
            T[i] += v[i]

    print("日期范围：%s ~ %s" % (dmin, dmax))
    print()
    print("总行数 %d ｜ 无效 %d（%.1f%%）｜ 有效 %d（%.1f%%）"
          % (total, sum(invalid.values()), sum(invalid.values()) / total * 100,
             T[0], T[0] / total * 100))
    for k, v in invalid.most_common():
        print("  %-12s %8d" % (k, v))
    print()
    print("=== 清洗后整体 ===")
    print("  单量 %d ｜ 金额 %.2f ｜ 退货单量 %d ｜ 退货金额 %.2f"
          % (T[0], T[1], T[2], T[3]))
    print("  单量退货率 %.2f%% ｜ 金额退货率 %.2f%%"
          % (T[2] / T[0] * 100, T[3] / T[1] * 100))

    print()
    print("=== 按月 ===")
    print("  %-9s %9s %14s %11s %10s %10s" % ("月份", "有效单量", "有效金额", "退货金额", "单量退货率", "金额退货率"))
    for ym in sorted(mon):
        n, a, rn, ra = mon[ym]
        print("  %-9s %9d %14.0f %11.0f %9.2f%% %9.2f%%"
              % (ym, n, a, ra, rn / n * 100 if n else 0, ra / a * 100 if a else 0))

    print()
    print("=== 按预售天数 ===")
    print("  %-10s %9s %14s %10s" % ("区间", "单量", "金额", "金额退货率"))
    for name in [b[0] for b in PRESALE_BUCKETS] + ["未发货", "无法计算"]:
        if name not in presale:
            continue
        n, a, rn, ra = presale[name]
        print("  %-10s %9d %14.0f %9.2f%%" % (name, n, a, ra / a * 100 if a else 0))
    if days_list:
        days_list.sort()
        print("  预售天数：中位 %.1f 天 ｜ 均值 %.1f 天 ｜ P90 %.1f 天"
              % (days_list[len(days_list) // 2], sum(days_list) / len(days_list),
                 days_list[int(len(days_list) * 0.9)]))

    out_m = os.path.join(EXPORTS, "return_rate_by_month_%s.csv" % label)
    with open(out_m, "w", encoding="utf-8-sig", newline="") as g:
        w = csv.writer(g)
        w.writerow(["月份", "有效单量", "有效金额", "退货单量", "退货金额",
                    "单量退货率", "金额退货率"])
        for ym in sorted(mon):
            n, a, rn, ra = mon[ym]
            w.writerow([ym, n, round(a, 2), rn, round(ra, 2),
                        round(rn / n, 4) if n else "", round(ra / a, 4) if a else ""])

    out_p = os.path.join(EXPORTS, "return_rate_by_product_%s.csv" % label)
    rows = []
    for pid, (n, a, rn, ra, name) in prod.items():
        if n < 10:
            continue
        rows.append([pid, name, n, round(a, 2), rn, round(ra, 2),
                     round(ra / a, 4) if a else "", round(rn / n, 4) if n else ""])
    rows.sort(key=lambda x: -x[3])
    with open(out_p, "w", encoding="utf-8-sig", newline="") as g:
        w = csv.writer(g)
        w.writerow(["商品ID", "商品名", "有效单量", "有效金额", "退货单量", "退货金额",
                    "金额退货率", "单量退货率"])
        w.writerows(rows)

    print()
    print("已写出：%s（%d 个月）" % (os.path.relpath(out_m, BASE), len(mon)))
    print("已写出：%s（%d 个商品）" % (os.path.relpath(out_p, BASE), len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
