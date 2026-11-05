"""固定日报卡片的字段完整性与飞书请求结构；不联网。"""
import json
import os
import sys
import tempfile
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import cards, notify


def main():
    totals = {k: D(v) for k, v in {
        "gmv": "10000", "ad_spend": "1000", "backend_net": "5000",
        "comprehensive_cost": "1100", "comprehensive_roi": "4.5454",
        "predicted_net": "4000", "estimated_total_cost": "3500", "predicted_profit": "500",
        "daily_spend": "166.666", "daily_cap_reference": "250",
        "daily_breakeven_spend": "300"}.items()}
    plans = []
    for i in range(5):
        plans.append({"plan_name": "计划%d" % i, "plan_id": "p%d" % i,
                      "gmv": D("2000"), "ad_spend": D("200"),
                      "comprehensive_roi": D("4.5"), "refund_rate": D("0.6"),
                      "predicted_profit": D("100"), "daily_spend": D("33.33"),
                      "daily_cap_reference": D("50"), "daily_breakeven_spend": D("60"),
                      "status": "在参考内"})
    result = {"season": "autumn_winter",
              "audit": {"start": "2026-10-15", "end": "2026-10-20", "days": 6,
                        "selected_rows": 43, "completeness_note": "仅验证文件内连续日期"},
              "totals": totals, "plans": plans}
    built = cards.build(result)
    assert len(built) == 3, "摘要 + 每4个计划一张"
    assert all(x["header"]["title"]["tag"] == "plain_text" for x in built)
    content = json.dumps(built, ensure_ascii=False)
    for name in ("后台实测", "历史退款情景预测", "整体支付 ROI", "计划0", "计划4",
                 "不是当期实退", "2026-10-20"):
        assert name in content, name
    assert "净ROI" not in content
    with tempfile.TemporaryDirectory() as tmp:
        output = cards.save(built, Path(tmp) / "model_x.md")
        assert json.loads(output.read_text(encoding="utf-8")) == built

    app = notify.FeishuAppNotifier("app", "secret", "oc_chat")
    app._tenant_token = lambda: "token"
    request = {}
    app._request = lambda method, url, body, headers: request.update(
        method=method, url=url, body=body, headers=headers) or {"code": 0}
    app.send_card(built[0])
    assert request["body"]["msg_type"] == "interactive"
    assert json.loads(request["body"]["content"]) == built[0]
    assert request["body"]["receive_id"] == "oc_chat"

    webhook = notify.FeishuNotifier("https://example.invalid", keyword="投流助手")
    posted = {}
    webhook._post = lambda body: posted.update(body) or True
    webhook.send_card(built[0])
    assert posted["msg_type"] == "interactive"
    assert "投流助手" in posted["card"]["header"]["title"]["content"]
    assert "投流助手" not in built[0]["header"]["title"]["content"], "不能改原模板"
    print("OK test_cards: 固定布局/全部计划/JSON存档/飞书应用与webhook请求")


if __name__ == "__main__":
    main()
