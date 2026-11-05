"""查「未发货」订单到底是什么状态。

我之前的「未发货退货率 90.6%」是按「发货时间为空」定义的，
但没验证这批订单后来怎么样了。这个脚本做交叉表：
  未发货 × 售后状态
  未发货 × 订单状态
  未发货 × 订单提交时间（看是不是集中在最近，即还在履约中）
"""
import csv
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORTS = os.path.join(BASE, "data", "exports")


def main():
    label = (sys.argv[1] if len(sys.argv) > 1 else "winter").strip().lower()
    src = os.path.join(EXPORTS, "orders_%s.csv" % label)
    f = open(src, encoding="utf-8-sig", newline="")
    r = csv.reader(f)
    head = [h.strip() for h in next(r)]
    ix = {h: i for i, h in enumerate(head)}

    i_pay, i_ship, i_cancel = ix["支付完成时间"], ix["发货时间"], ix["取消原因"]
    i_after, i_status = ix["售后状态"], ix["订单状态"]
    i_time = ix["订单提交时间"]

    after = Counter()
    st = Counter()
    months = Counter()
    n_unshipped = 0
    n_all = 0

    for row in r:
        if len(row) <= max(i_pay, i_ship, i_after, i_status):
            continue
        if not row[i_pay].strip() or row[i_cancel].strip():
            continue
        n_all += 1
        if row[i_ship].strip():
            continue
        n_unshipped += 1
        after[row[i_after].strip() or "(空)"] += 1
        st[row[i_status].strip() or "(空)"] += 1
        months[row[i_time].strip()[:7]] += 1
    f.close()

    print("=== %s ===" % label)
    print("有效订单 %d ｜ 其中未发货 %d（%.1f%%）"
          % (n_all, n_unshipped, n_unshipped / n_all * 100 if n_all else 0))
    print()
    print("未发货 × 售后状态：")
    for k, v in after.most_common():
        print("  %-14s %8d  %6.2f%%" % (k, v, v / n_unshipped * 100))
    print()
    print("未发货 × 订单状态：")
    for k, v in st.most_common():
        print("  %-14s %8d  %6.2f%%" % (k, v, v / n_unshipped * 100))
    print()
    print("未发货 × 订单月份（看是否集中在最近 = 还在履约中）：")
    for k in sorted(months):
        print("  %-9s %8d  %6.2f%%" % (k, months[k], months[k] / n_unshipped * 100))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
