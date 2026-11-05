"""按商品单价分层，重算金额退货率。

分层：≤100 / 101-200 / 201-300 / 301+
口径与 season_return_rate.py 一致：
  有效订单 = 有支付完成时间 且 取消原因为空
  退货     = 售后状态 ∈ {退款成功, 已全额退款}（含发货前仅退款）
  金额     = 订单应付金额
  单价     = 商品单价（用于分层）

用法：python tools/price_band_return.py
"""
import csv
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORTS = os.path.join(BASE, "data", "exports")

REFUND_STATUS = ("退款成功", "已全额退款")
SEASONS = [("夏季", "summer", "2026-05-01", "2026-09-30"),
           ("秋冬", "winter", "2025-10-01", "2025-12-31")]
BANDS = [("≤100", 0, 100), ("101-200", 100.0001, 200),
         ("201-300", 200.0001, 300), ("301+", 300.0001, 10 ** 9)]


def band_of(price):
    for name, lo, hi in BANDS:
        if lo <= price <= hi:
            return name
    return "无单价"


def run(label, dmin, dmax):
    src = os.path.join(EXPORTS, "orders_%s.csv" % label)
    f = open(src, encoding="utf-8-sig", newline="")
    r = csv.reader(f)
    head = [h.strip() for h in next(r)]
    ix = {h: i for i, h in enumerate(head)}

    i_pay, i_cancel = ix["支付完成时间"], ix["取消原因"]
    i_amt, i_after = ix["订单应付金额"], ix["售后状态"]
    i_time, i_price = ix["订单提交时间"], ix["商品单价"]
    i_qty = ix["商品数量"]

    agg = {}
    qty1 = 0
    valid = 0

    for row in r:
        if len(row) <= max(i_pay, i_cancel, i_amt, i_after, i_price, i_qty):
            continue
        if not (dmin <= row[i_time].strip()[:10] <= dmax):
            continue
        if not row[i_pay].strip() or row[i_cancel].strip():
            continue
        valid += 1

        def num(idx):
            try:
                return float(row[idx].strip().replace(",", "") or 0)
            except ValueError:
                return 0.0

        amt = num(i_amt)
        price = num(i_price)
        if int(num(i_qty)) == 1:
            qty1 += 1
        ref = 1 if row[i_after].strip() in REFUND_STATUS else 0

        b = agg.setdefault(band_of(price), [0, 0.0, 0, 0.0])
        b[0] += 1
        b[1] += amt
        b[2] += ref
        b[3] += amt if ref else 0.0
    f.close()
    return agg, valid, qty1


def main():
    for name, label, dmin, dmax in SEASONS:
        agg, valid, qty1 = run(label, dmin, dmax)
        print("=== %s（%s ~ %s）===" % (name, dmin, dmax))
        print("有效订单 %d ｜ 其中单件订单 %d（%.1f%%）" % (valid, qty1, qty1 / valid * 100))
        print()
        print("  %-10s %10s %14s %10s %10s %10s"
              % ("价格带", "有效单量", "有效金额", "退货单量", "单量退货率", "金额退货率"))
        order = [b[0] for b in BANDS] + ["无单价"]
        tot = [0, 0.0, 0, 0.0]
        for bn in order:
            if bn not in agg:
                continue
            n, a, rn, ra = agg[bn]
            if n == 0:
                continue
            print("  %-10s %10d %14.0f %10d %9.2f%% %9.2f%%"
                  % (bn, n, a, rn, rn / n * 100, ra / a * 100 if a else 0))
            tot[0] += n; tot[1] += a; tot[2] += rn; tot[3] += ra
        print("  %-10s %10d %14.0f %10d %9.2f%% %9.2f%%"
              % ("合计", tot[0], tot[1], tot[2], tot[2] / tot[0] * 100, tot[3] / tot[1] * 100))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
