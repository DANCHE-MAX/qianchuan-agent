"""推送层测试：字节截断、dry-run 降级、企业微信与飞书两种渠道。"""
import copy
import base64
import hashlib
import hmac
import json
import io
import os
import sys
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import notify, settings


def main():
    # 1. 短文本不动
    assert notify.truncate_bytes("短消息", 100) == "短消息"

    # 2. 超长按字节截断，且不能切碎中文
    text = "广告投放" * 2000
    out = notify.truncate_bytes(text, 2048)
    assert len(out.encode("utf-8")) <= 2100, "截断后应接近上限，实际 %d" % len(out.encode("utf-8"))
    assert out.endswith("）"), "应有截断提示"
    out.encode("utf-8").decode("utf-8")  # 不会抛异常 = 没切碎中文

    # 3. 没配 webhook 时必须降级而不是报错
    # 注意：不要依赖用户 rules.json 里的 channel，测试要自己指定，否则真实配置一变就红。
    cfg = settings.load_config()
    cfg_wecom = copy.deepcopy(cfg)
    cfg_wecom["notify"]["channel"] = "wecom"

    nf = notify.build_notifier(cfg_wecom, {})
    assert isinstance(nf, notify.ConsoleNotifier), "没有 webhook 应降级为控制台"
    nf.send_text("测试消息", at_roles=["投手"])
    nf.send_markdown("**测试**")
    assert nf.name == "console(dry-run)"

    # 4. 配了企业微信 webhook 选企微实现
    nf2 = notify.build_notifier(cfg_wecom, {"WECOM_WEBHOOK": "https://example.com/hook?key=x"})
    assert isinstance(nf2, notify.WeComNotifier)
    assert nf2.md_limit == cfg["notify"]["markdown_max_bytes"]
    # roles 里的 _comment 不能被当成角色
    assert "_comment" not in nf2.roles

    # 5. 切到飞书：配置能生效，消息结构正确（不发网络）
    cfg_fs = copy.deepcopy(cfg)
    cfg_fs["notify"]["channel"] = "feishu"
    cfg_fs["notify"]["roles"] = {"投手": ["ou_test123"], "老板": []}

    nf3 = notify.build_notifier(cfg_fs, {"FEISHU_WEBHOOK": "https://x/hook/y"})
    assert isinstance(nf3, notify.FeishuNotifier), "channel=feishu 应选飞书实现"

    sent = {}

    def fake_post(body):
        sent.clear()
        sent.update(body)
        return True

    nf3._post = fake_post

    nf3.send_markdown("**标题**\n> 引用行\n普通行")
    assert sent["msg_type"] == "interactive", "飞书 markdown 走消息卡片"
    content = sent["card"]["elements"][0]["text"]["content"]
    assert content.split("\n")[1] == "引用行", "行首的 > 应被去掉（lark_md 不支持引用块）"

    nf3.send_text("余额不足", at_roles=["投手"])
    assert sent["msg_type"] == "interactive", "需要 @ 人时走卡片（lark_md 的 at 更可靠）"
    assert '<at user_id="ou_test123"></at>' in sent["card"]["elements"][0]["text"]["content"]

    # 6. 飞书没配 webhook 也要降级
    nf4 = notify.build_notifier(cfg_fs, {})
    assert isinstance(nf4, notify.ConsoleNotifier)

    # 7. 签名校验（机器人开启该选项时不带 sign 会报 19021）
    nf5 = notify.FeishuNotifier("https://x/hook/z", secret="sec123", keyword="投流助手")
    body = nf5._signed_body({"msg_type": "text"})
    assert "timestamp" in body and "sign" in body
    expect = base64.b64encode(
        hmac.new(("%s\nsec123" % body["timestamp"]).encode("utf-8"),
                 digestmod=hashlib.sha256).digest()).decode("utf-8")
    assert body["sign"] == expect, "签名算法必须与飞书文档一致"

    # 8. 关键词：消息里没有关键词时自动补在开头
    sent2 = {}
    nf5._post = lambda b: sent2.update(b) or True
    nf5.send_text("余额不足")
    assert sent2["content"]["text"].startswith("【投流助手】"), "关键词应被注入开头"
    assert sent2["msg_type"] == "text", "不需要 @ 时保持纯文本"

    # 9. @ 用邮箱：不用去查 open_id
    nf6 = notify.FeishuNotifier("https://x/hook/e", roles={"运维": ["ops@company.com"]})
    sent3 = {}
    nf6._post = lambda b: sent3.update(b) or True
    nf6.send_text("服务异常", at_roles=["运维"])
    assert '<at email="ops@company.com"></at>' in sent3["card"]["elements"][0]["text"]["content"]

    # 10. 自建应用：token 只取一次（缓存）、content 必须是 JSON 字符串
    calls = []

    def fake_request(method, url, body=None, headers=None):
        calls.append((url, body, headers or {}))
        if "tenant_access_token" in url:
            return {"code": 0, "tenant_access_token": "t-123", "expire": 7200}
        return {"code": 0}

    nf7 = notify.FeishuAppNotifier("cli_x", "sec", "oc_chat1",
                                   roles={"投手": ["ou_a"]})
    nf7._request = fake_request
    nf7.send_text("余额不足", at_roles=["投手"])
    nf7.send_text("第二条")

    token_calls = [c for c in calls if "tenant_access_token" in c[0]]
    assert len(token_calls) == 1, "token 要缓存，不能每条消息都重新取"

    msg_url, msg_body, msg_head = [c for c in calls if "im/v1/messages" in c[0]][0]
    assert isinstance(msg_body["content"], str), "im/v1 的 content 必须是 JSON 字符串"
    assert msg_body["receive_id"] == "oc_chat1"
    assert msg_head["Authorization"] == "Bearer t-123"
    assert "ou_a" in json.loads(msg_body["content"])["text"], "@ 要写进消息正文"

    # 11. @ 白名单只剩一个群时可作为日报目标；多个群必须显式指定。
    cfg_app = copy.deepcopy(cfg)
    cfg_app["notify"]["channel"] = "feishu_app"
    creds = {"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret"}
    one = notify.build_notifier(cfg_app, dict(creds, FEISHU_ALLOWED_CHAT_IDS="oc_only"))
    assert isinstance(one, notify.FeishuAppNotifier) and one.chat_id == "oc_only"
    many = notify.build_notifier(cfg_app, dict(creds, FEISHU_ALLOWED_CHAT_IDS="oc_one,oc_two"))
    assert isinstance(many, notify.ConsoleNotifier)
    explicit = notify.build_notifier(cfg_app, dict(creds, FEISHU_ALLOWED_CHAT_IDS="oc_one,oc_two",
                                                   FEISHU_CHAT_ID="oc_two"))
    assert isinstance(explicit, notify.FeishuAppNotifier) and explicit.chat_id == "oc_two"
    try:
        notify.build_notifier(cfg_app, dict(creds, FEISHU_CHAT_ID="oc_one,oc_two"))
        assert False, "多个群 ID 不能作为一个日报目标"
    except notify.NotifierConfigError:
        pass

    # 12. HTTP 400 展示飞书错误正文，且不重复发三次相同的坏请求。
    bad = notify.FeishuAppNotifier("cli_x", "secret", "oc_chat")
    bad._tenant_token = lambda: "test-token"
    called = []
    def reject(req, timeout):
        called.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(b'{"code":230001,"msg":"invalid receive_id"}'))
    with patch.object(notify.urllib.request, "urlopen", side_effect=reject):
        try:
            bad.send_text("test")
            assert False, "HTTP 400 应失败"
        except RuntimeError as exc:
            assert "HTTP 400" in str(exc) and "230001" in str(exc)
    assert len(called) == 1

    print("OK test_notify: 截断/降级/企微/飞书webhook/签名/关键词/邮箱@/自建应用 全部通过")


if __name__ == "__main__":
    main()
