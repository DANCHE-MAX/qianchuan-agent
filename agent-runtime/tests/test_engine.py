"""规则引擎测试：不需要 pytest，直接 python tests/test_engine.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import engine, settings, sources


def main():
    cfg = settings.load_config()
    # 旧演示引擎测试使用固定夹具，生产日报已路由到新模型。
    cfg['breakeven'] = {'direct_roi': 3.3, 'gross_margin_default': .30}
    plans = sources.MockSource().fetch()
    assert len(plans) == 17, "演示数据应该是 17 条，实际 %d" % len(plans)

    history = engine.load_history(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "action_history.json"))

    from datetime import datetime
    decisions = engine.run(plans, cfg, history, now=datetime(2026, 10, 20, 16, 30))
    assert len(decisions) == len(plans), "每个计划都要有一条决策"
    assert all(d["decision_id"] for d in decisions), "决策必须有 ID"

    rules = {d["rule_id"] for d in decisions}
    for expected in ("R001", "R002", "R003", "R004", "R005", "R006", "R-SKIP"):
        assert expected in rules, "演示数据应覆盖 %s，实际命中 %s" % (expected, sorted(rules))

    executable = [d for d in decisions if d["executable"]]
    blocked = [d for d in decisions if d["gate_blocks"] and d["action"] in
               ("decrease", "increase", "pause")]
    assert executable, "应该至少有一条可执行动作"
    assert blocked, "应该至少有一条被风控闸门拦下"

    # 试运行永远不能真的执行
    for d in decisions:
        assert d["execution"]["can_execute"] is False, "只读模式不允许 can_execute=True"

    # 严重度必须排在前面的比后面的严重
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    sev = [order[d["severity"]] for d in decisions]
    assert sev == sorted(sev), "决策必须按严重度排序"

    print("OK test_engine: %d 条决策，规则覆盖 %d 种，可执行 %d，被拦 %d"
          % (len(decisions), len(rules), len(executable), len(blocked)))


if __name__ == "__main__":
    main()
