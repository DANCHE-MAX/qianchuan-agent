"""扫描抖店订单明细，算各商品的退货率（金额口径 + 单量口径）。

为什么要单独一个脚本：源文件 280MB、几十万行，一条命令扫不完；
而且中文列名不能塞进 python -c（控制台编码会弄坏）。

用法：python tools/scan_orders.py
输出：data/exports/return_rate_by_product.csv（商品级退货率，给日报用）
"""
import csv
import glob
import os

REFUND_KEYWORDS = ("退款", "退货")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "exports", "return_rate_by_product.csv")


def main():
    paths = glob.glob(os.path.join(BASE, "data", "exports", "*.csv"))
    paths = [p for p in paths if "return_rate" not in p]
    if not paths:
        print("没找到源文件（data/exports/*.csv）")
        return 1
    src = paths[0]
    print("源文件：%s（%.0f MB）" % (os.path.basename(src),
                                os.path.getsize(src) / 1024 / 1024))

    f = open(src, encoding="utf-8-sig", newline="")
    reader = csv.reader(f)
    head = [h.strip() for h in next(reader)]
    ix = {h: i for i, h in enumerate(head)}

    need = ["商品ID", "选购商品", "订单应付金额", "订单提交时间", "售后状态"]
    missing = [k for k in need if k not in ix]
    if missing:
        print("缺少列：%s" % missing)
        return 1

    i_pid, i_name = ix["商品ID"], ix["选购商品"]
    i_amt, i_time, i_after = ix["订单应付金额"], ix["订单提交时间"], ix["售后状态"]

    # per_product[pid] = {状态: [单数, 金额]}, meta[name]
    per_product = {}
    meta = {}
    status_all = {}
    months = {}
    n = 0
    dmin, dmax = "9999", ""

    for row in reader:
        if len(row) <= i_after:
            continue
        n += 1
        pid = row[i_pid].strip().strip('"').strip()
        if not pid:
            continue
        status = row[i_after].strip() or "(空)"
        try:
            amt = float(row[i_amt].strip().replace(",", "") or 0)
        except ValueError:
            amt = 0.0
        date = row[i_time].strip()[:10]
        ym = date[:7]

        bucket = per_product.setdefault(pid, {})
        s = bucket.setdefault(status, [0, 0.0])
        s[0] += 1
        s[1] += amt
        if pid not in meta:
            meta[pid] = row[i_name].strip()[:30]

        g = status_all.setdefault(status, [0, 0.0])
        g[0] += 1
        g[1] += amt

        m = months.setdefault(ym, [0, 0.0, 0, 0.0])
        m[0] += 1
        m[1] += amt
        if any(k in status for k in REFUND_KEYWORDS):
            m[2] += 1
            m[3] += amt

        if date:
            dmin = min(dmin, date)
            dmax = max(dmax, date)

    f.close()

    print("总行数：%d ｜ 日期范围：%s ~ %s ｜ 商品数：%d"
          % (n, dmin, dmax, len(per_product)))
    print()
    print("=== 售后状态分布（全量） ===")
    for k, (c, a) in sorted(status_all.items(), key=lambda kv: -kv[1][0]):
        print("  %-16s 单量 %7d  金额 %12.2f  (%5.1f%%)"
              % (k[:16], c, a, c / n * 100 if n else 0))

    print()
    print("=== 按月：退货率趋势 ===")
    print("  %-9s %8s %12s %8s %8s" % ("月份", "单量", "金额", "单量退货率", "金额退货率"))
    for ym in sorted(months):
        c, a, rc, ra = months[ym]
        print("  %-9s %8d %12.2f %7.1f%% %7.1f%%"
              % (ym, c, a, rc / c * 100 if c else 0, ra / a * 100 if a else 0))

    # 商品级退货率（金额口径），只保留样本足够的
    rows = []
    for pid, bucket in per_product.items():
        tot_n = sum(v[0] for v in bucket.values())
        tot_a = sum(v[1] for v in bucket.values())
        ref_n = sum(v[0] for k, v in bucket.items() if any(x in k for x in REFUND_KEYWORDS))
        ref_a = sum(v[1] for k, v in bucket.items() if any(x in k for x in REFUND_KEYWORDS))
        if tot_n < 10:      # 少于 10 单不算，避免小样本
            continue
        rows.append([pid, meta.get(pid, ""), tot_n, round(tot_a, 2),
                     ref_n, round(ref_a, 2),
                     round(ref_a / tot_a, 4) if tot_a else "",
                     round(ref_n / tot_n, 4) if tot_n else ""])

    rows.sort(key=lambda r: -r[3])
    with open(OUT, "w", encoding="utf-8-sig", newline="") as g:
        w = csv.writer(g)
        w.writerow(["商品ID", "商品名", "总单量", "总金额", "退款单量", "退款金额",
                    "金额退货率", "单量退货率"])
        w.writerows(rows)

    print()
    print("=== 商品级退货率 TOP10（按金额，样本≥10单） ===")
    print("  %-16s %-20s %6s %10s %7s" % ("商品ID", "商品名", "单量", "金额", "金额退货率"))
    for r in rows[:10]:
        print("  %-16s %-20s %6d %10.0f %6.1f%%"
              % (r[0][:16], r[1][:20], r[2], r[3], r[6] * 100))
    print()
    print("已写出：%s（%d 个商品）" % (os.path.relpath(OUT, BASE), len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
