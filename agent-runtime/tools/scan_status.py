"""看一眼订单的状态字段取值，用来定清洗规则。不改源文件。"""
import csv
import glob
import os
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    paths = [p for p in glob.glob(os.path.join(BASE, "data", "exports", "*.csv"))
             if "return_rate" not in p]
    src = paths[0]
    f = open(src, encoding="utf-8-sig", newline="")
    r = csv.reader(f)
    head = [h.strip() for h in next(r)]
    ix = {h: i for i, h in enumerate(head)}

    cols = ["订单状态", "取消原因", "售后状态", "订单类型"]
    counters = {c: Counter() for c in cols if c in ix}
    time_samples = []
    n = 0

    i_st = ix.get("订单状态")
    for row in r:
        if len(row) < len(head):
            continue
        n += 1
        for c, cnt in counters.items():
            cnt[row[ix[c]].strip() or "(空)"] += 1
        if n <= 3:
            time_samples.append((
                row[ix["订单提交时间"]].strip(),
                row[ix["支付完成时间"]].strip() if "支付完成时间" in ix else "",
                row[ix["发货时间"]].strip() if "发货时间" in ix else "",
            ))
    f.close()

    print("总行数：%d" % n)
    print()
    for c in cols:
        if c not in ix:
            print("缺列：%s" % c)
            continue
        print("=== %s (%d 种取值) ===" % (c, len(counters[c])))
        for k, v in counters[c].most_common(20):
            print("  %-18s %8d  %5.1f%%" % (k[:18], v, v / n * 100))
        print()

    print("=== 时间字段格式样例 ===")
    for s in time_samples:
        print("  提交=%r  支付=%r  发货=%r" % s)


if __name__ == "__main__":
    main()
