"""飞书入站与只读报告查询的本地测试，不连接网络、不发送群消息。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import listener, notify


def main():
    with tempfile.TemporaryDirectory() as tmp:
        report = {
            "season": "autumn_winter",
            "audit": {"start": "2026-10-15", "end": "2026-10-20", "days": 6},
            "totals": {"gmv": "10000", "ad_spend": "1000", "comprehensive_roi": "3.5",
                       "predicted_net": "4000", "predicted_profit": "500",
                       "daily_cap_reference": "250", "daily_breakeven_spend": "300"},
            "plans": [{"plan_name": "托管", "plan_id": "123", "gmv": "8000",
                       "ad_spend": "800", "comprehensive_roi": "3.2",
                       "predicted_profit": "400", "daily_cap_reference": "200"}],
        }
        path = Path(tmp) / "model_2026-10-15_2026-10-20_autumn_winter.json"
        path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        assert "2026-10-20" in listener.answer("报告", tmp)
        assert "后台实测" in listener.answer("报告", tmp)
        assert "模型预测" in listener.answer("报告", tmp)
        assert "托管" in listener.answer("计划 托管", tmp)
        assert "没有找到夏季" in listener.answer("报告 夏季", tmp)
        assert "帮助" not in listener.answer("报告", tmp)
        assert "可用指令" in listener.answer("帮助", tmp)

        inbox = listener.Inbox(Path(tmp) / "listener.db")
        assert inbox.add("msg-1", "报告")
        assert not inbox.add("msg-1", "报告"), "重复事件不应重复回复"
        assert inbox.next()[:2] == ("msg-1", "报告")
        inbox.failed("msg-1", 0)
        assert inbox.next() is None, "失败后的重试需要退避"
        inbox.done("msg-1")
        assert inbox.next() is None

    event = {"event": {"message": {"chat_type": "group", "message_type": "text",
                                  "message_id": "msg-2", "chat_id": "oc_group",
                                  "content": json.dumps({"text": "@_user_1 报告"})}}}
    assert listener._extract(event) == ("msg-2", "oc_group", "报告")
    event["event"]["message"]["content"] = json.dumps({"text": "报告"})
    assert listener._extract(event) is None, "普通群消息不能触发回复"
    event["event"]["message"]["chat_type"] = "p2p"
    assert listener._extract(event) is None

    nf = notify.FeishuAppNotifier("app", "secret", "oc_default")
    nf._tenant_token = lambda: "test-token"
    captured = {}
    nf._request = lambda method, url, body, headers: captured.update(
        method=method, url=url, body=body, headers=headers) or {"code": 0}
    nf.reply_text("om_test", "报告")
    assert captured["url"].endswith("/im/v1/messages/om_test/reply")
    assert "oc_default" not in json.dumps(captured, ensure_ascii=False)
    assert json.loads(captured["body"]["content"])["text"] == "报告"
    print("OK test_listener: 查询/实测预测标签/季节/入站过滤/去重/原消息回复")


if __name__ == "__main__":
    main()
