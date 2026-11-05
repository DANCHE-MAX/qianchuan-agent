"""余额预警测试：速率、分档、状态机。"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import balance, settings


def main():
    cfg = settings.load_config()
    now = datetime(2026, 10, 21, 16, 40)
    fresh = "2026-10-21 16:00"

    # 1. 两次快照 → 真实窗口速率
    rate, src = balance.compute_rate(
        [{"h": 16, "s": 1200}, {"h": 15, "s": 900}, {"h": 14, "s": 600}], fallback_hour=16)
    assert src == "window", "有多次快照应该用窗口速率"
    assert abs(rate - 300) == 0, "速率应为 (1200-600)/2 = 300，实际 %s" % rate

    # 2. 只有一次快照 → 估算并标记
    rate, src = balance.compute_rate([{"h": 16, "s": 1600}], fallback_hour=16)
    assert src == "avg_today" and abs(rate - 100) < 0.01, "单快照应退化为今日均值"

    # 3. 数据回滚（跨天导致 delta<=0）不能算出负速率
    rate, src = balance.compute_rate([{"h": 2, "s": 100}, {"h": 23, "s": 9000}], fallback_hour=2)
    assert rate >= 0 and src != "window", "负增长必须退回估算，不能给负数"

    # 4. 三档分档
    urgent = balance.evaluate("QC-A02", 400, fresh, rate=150, cfg=cfg, now=now)
    assert urgent["level"] == balance.URGENT, "ETA 2.7h < 3.5h 应为 urgent"
    assert urgent["topup"] == 7600, "建议充值应为 上限8000-余额400"

    hint = balance.evaluate("QC-A01", 8600, fresh, rate=450, cfg=cfg, now=now)
    assert hint["level"] == balance.HINT, "ETA 19h 应为 hint"

    normal = balance.evaluate("XHS-A01", 5000, fresh, rate=140, cfg=cfg, now=now)
    assert normal["level"] == balance.NORMAL, "ETA 35h 应为 normal"

    # 5. 数据过期优先于一切
    stale = balance.evaluate("QC-A02", 400, "2026-10-21 08:00", rate=150, cfg=cfg, now=now)
    assert stale["level"] == balance.STALE, "8 小时前的数据应为 stale（阈值 4h）"

    # 6. 状态机
    assert balance.decide_action(balance.URGENT, None) == "warn", "首次告警应发提醒"
    assert balance.decide_action(balance.URGENT, {"level": "urgent", "escalated": 0},
                                 30, 60) == "silent", "同档位且未到时长，不打扰"
    assert balance.decide_action(balance.URGENT, {"level": "urgent", "escalated": 0},
                                 70, 60) == "escalate", "超过 60 分钟应升级"
    assert balance.decide_action(balance.NORMAL, {"level": "urgent", "escalated": 1},
                                 ) == "recover", "从告警回到正常应消警"
    assert balance.decide_action(balance.STALE, None) == "stale", "数据过期单独一档"

    print("OK test_balance: 速率/分档/状态机 全部通过")


if __name__ == "__main__":
    main()
