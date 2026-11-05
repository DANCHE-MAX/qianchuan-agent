"""查订单里的流量来源列，看能不能把「广告订单」单独拆出来。

这件事很重要：现在修正千川 ROI 用的是「全店退货率」，
但如果订单能按流量来源拆，就能算出「广告订单的退货率」，那才是正确口径。
"""
import csv
import os
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORTS = os.path.join(BASE, "data", "exports")

REFUND_STATUS = ("退款成功", "已全额退款")


def main():
    label = (sys.argv[1] if len(sys.argv) > 1 else "winter").strip().lower()
    src = os.path.join(EXPORTS, "orders_%s.csv" % label)
    f = open(src, encoding="utf-8-sig", newline="")
    r = csv.reader(f)
    head = [h.strip() for h in next(r)]
    ix = {h: i for i, h in enumerate(head)}

    cols = [c for c in ("广告渠道", "流量类型", "流量体裁", "流量渠道") if c in ix]
    print("可用的流量列：%s" % cols)
    if not cols:
        print("没有流量来源列，无法拆分")
        return 1

    counters = {c: Counter() for c in cols}
    # 按「流量类型」聚合金额与退货
    agg = defaultdict(lambda: [0, 0.0, 0, 0.0])
    n = 0

    i_pay, i_cancel = ix["支付完成时间"], ix["取消原因"]
    i_amt, i_after = ix["订单应付金额"], ix["售后状态"]

    for row in r:
        if len(row) <= max(i_pay, i_cancel, i_amt, i_after):
            continue
        if not row[i_pay].strip() or row[i_cancel].strip():
            continue
        n += 1
        for c in cols:
            counters[c][row[ix[c]].strip() or "(空)"] += 1
        key = tuple(row[ix[c]].strip() or "(空)" for c in cols)
        try:
            amt = float(row[i_amt].strip().replace(",", "") or 0)
        except ValueError:
            amt = 0.0
        ref = 1 if row[i_after].strip() in REFUND_STATUS else 0
        a = agg[key]
        a[0] += 1; a[1] += amt; a[2] += ref; a[3] += amt if ref else 0.0

    f.close()

    print("有效订单：%d\n" % n)
    for c in cols:
        print("=== %s（%d 种取值）===" % (c, len(counters[c])))
        for k, v in counters[c].most_common(12):
            print("  %-26s %8d  %5.1f%%" % (k[:26], v, v / n * 100))
        print()

    print("=== 按多列组合（TOP15，按金额）===")
    print("  %-52s %8s %12s %8s" % ("组合", "单量", "金额", "金额退货率"))
    for k, (c, a, rc, ra) in sorted(agg.items(), key=lambda kv: -kv[1][1])[:15]:
        print("  %-52s %8d %12.0f %7.1f%%" % (" | ".join(k)[:52], c, a, ra / a * 100 if a else 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
