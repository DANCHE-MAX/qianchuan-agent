"""推送通道适配层。

为什么要有这一层：业务代码永远只调 send_text/send_markdown，
换渠道（企微 → 飞书 → 钉钉）只改这里和配置，业务一行不动。

三个必须处理的实际问题：
1. 企业微信 text 上限 2048 字节、markdown 上限 4096 字节 —— 超了会报错或截断；
2. @ 人只在 text 类型有效，markdown 不支持；
3. 网络抖动要重试，但业务错误（errcode != 0）不能重试。
"""
import base64
import copy
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode
from .reliability import DeliveryError
from . import diagnostics

RETRY_TIMES = 3
TIMEOUT_SECONDS = 10


class NotifierConfigError(ValueError):
    """推送配置错误，应在本地修正，不作为系统故障向群告警。"""


def truncate_bytes(text, limit, note="\n…（内容过长已截断）"):
    """按 UTF-8 字节数截断，不会切碎中文。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    cut = raw[: max(0, limit - len(note.encode("utf-8")))].decode("utf-8", errors="ignore")
    return cut + note


class Notifier:
    """所有渠道的统一接口。业务代码只依赖它。"""

    name = "base"

    def send_text(self, content, at_roles=None):
        raise NotImplementedError

    def send_markdown(self, content):
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """dry-run：只打印到控制台，不发任何网络请求。

    这样在没配 webhook 的时候，整条链路也能跑通、能验收。
    """

    name = "console(dry-run)"

    def send_text(self, content, at_roles=None):
        print("\n----- [dry-run text] -----")
        print(content)
        if at_roles:
            print("(将 @ %s)" % "、".join(at_roles))
        print("--------------------------")

    def send_markdown(self, content):
        print("\n----- [dry-run markdown] -----")
        print(content)
        print("------------------------------")


class WeComNotifier(Notifier):
    """企业微信群机器人。

    webhook 形如：
      https://example.invalid/webhook
    """

    name = "wecom"

    def __init__(self, webhook, roles=None, md_limit=4096, text_limit=2048):
        self.webhook = webhook
        self.roles = roles or {}
        self.md_limit = md_limit
        self.text_limit = text_limit

    # ---- 内部：发请求 + 重试 ----
    def _post(self, body):
        last_error = None
        for attempt in range(RETRY_TIMES):
            try:
                req = urllib.request.Request(
                    self.webhook,
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("errcode") == 0:
                    return True
                # 业务错误重试没有意义，直接抛
                raise RuntimeError(
                    "企业微信返回错误 errcode=%s errmsg=%s"
                    % (payload.get("errcode"), payload.get("errmsg"))
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == RETRY_TIMES - 1:
                    raise RuntimeError("推送失败（已重试 %d 次）：%s" % (RETRY_TIMES, exc))
                time.sleep(2 ** attempt)
        raise RuntimeError(str(last_error))

    # ---- 对外 ----
    def send_text(self, content, at_roles=None):
        targets = []
        for role in at_roles or []:
            targets.extend(self.roles.get(role, []))
        body = {
            "msgtype": "text",
            "text": {
                "content": truncate_bytes(content, self.text_limit),
                # 手机号和 userid 都能触达；这里统一用手机号，最省事
                "mentioned_mobile_list": targets,
            },
        }
        return self._post(body)

    def send_markdown(self, content):
        body = {
            "msgtype": "markdown",
            "markdown": {"content": truncate_bytes(content, self.md_limit)},
        }
        return self._post(body)


class FeishuNotifier(Notifier):
    """飞书群机器人。

    webhook 形如：
      https://example.invalid/webhook

    相比企业微信的两个优势：
      1. markdown 里可以直接 @ 人（企微只有 text 类型能 @）；
      2. 频控更宽（100 次/分钟），消息长度上限大得多。

    一个需要注意的差异：飞书的 lark_md 不支持 `>` 引用块，
    所以这里会把行首的 "> " 去掉，否则会原样显示成大于号。
    """

    name = "feishu"

    def __init__(self, webhook, roles=None, secret=None, keyword=None,
                 md_limit=20000, text_limit=20000):
        self.webhook = webhook
        self.roles = roles or {}
        self.secret = secret
        self.keyword = keyword
        self.md_limit = md_limit
        self.text_limit = text_limit

    def _signed_body(self, body):
        """机器人开了「签名校验」时必须带 timestamp + sign，否则返回 19021。"""
        if not self.secret:
            return body
        timestamp = str(int(time.time()))
        string_to_sign = "%s\n%s" % (timestamp, self.secret)
        digest = hmac.new(string_to_sign.encode("utf-8"),
                          digestmod=hashlib.sha256).digest()
        body = dict(body)
        body["timestamp"] = timestamp
        body["sign"] = base64.b64encode(digest).decode("utf-8")
        return body

    def _post(self, body):
        body = self._signed_body(body)
        last_error = None
        for attempt in range(RETRY_TIMES):
            try:
                req = urllib.request.Request(
                    self.webhook,
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                # 飞书不同版本分别返回 code / StatusCode
                code = payload.get("code", payload.get("StatusCode"))
                if code == 0:
                    return True
                raise RuntimeError(
                    "飞书返回错误 code=%s msg=%s"
                    % (code, payload.get("msg") or payload.get("StatusMessage"))
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == RETRY_TIMES - 1:
                    raise RuntimeError("推送失败（已重试 %d 次）：%s" % (RETRY_TIMES, exc))
                time.sleep(2 ** attempt)
        raise RuntimeError(str(last_error))

    def _targets(self, at_roles):
        ids = []
        for role in at_roles or []:
            ids.extend(self.roles.get(role, []))
        return ids

    @staticmethod
    def _at_tag(ident):
        """@ 的两种写法：邮箱（不用查 open_id，最省事）或 open_id。"""
        if "@" in ident:
            return '<at email="%s"></at>' % ident
        return '<at user_id="%s"></at>' % ident

    def _with_keyword(self, text):
        """机器人开了「自定义关键词」时，消息必须包含关键词，否则被拒。"""
        if self.keyword and self.keyword not in text:
            return "【%s】\n%s" % (self.keyword, text)
        return text

    def _card_body(self, text):
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "elements": [{
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": text},
                }],
            },
        }

    def send_text(self, content, at_roles=None):
        text = self._with_keyword(truncate_bytes(content, self.text_limit))
        ids = self._targets(at_roles)
        if not ids:
            return self._post({"msg_type": "text", "content": {"text": text}})
        # 需要 @ 人时改走卡片：lark_md 的 <at> 比 text 类型更可靠，且支持邮箱
        text += "\n" + " ".join(self._at_tag(i) for i in ids)
        return self._post(self._card_body(text))

    def send_markdown(self, content):
        text = _strip_blockquote(truncate_bytes(content, self.md_limit))
        return self._post(self._card_body(self._with_keyword(text)))

    def send_card(self, card):
        """向群机器人 webhook 发送结构化卡片。"""
        card = copy.deepcopy(card)
        if self.keyword:
            header = card.setdefault("header", {})
            title = header.setdefault("title", {"tag": "plain_text", "content": ""})
            if self.keyword not in title.get("content", ""):
                title["content"] = "【%s】 %s" % (self.keyword, title.get("content", ""))
        return self._post({"msg_type": "interactive", "card": card})


def _strip_blockquote(text):
    """飞书 lark_md 不支持 `>` 引用块，去掉行首符号，避免显示成字面的 >。"""
    return "\n".join(
        line[2:] if line.startswith("> ") else ("" if line == ">" else line)
        for line in text.split("\n")
    )


class FeishuAppNotifier(Notifier):
    """飞书自建应用（开放平台创建的机器人）。

    和群自定义机器人的区别：
      - 要先用 app_id/app_secret 换 tenant_access_token（2 小时有效，这里做了缓存）；
      - 发消息调 im/v1/messages，必须指定 receive_id（这里是群的 chat_id）；
      - content 要传 JSON **字符串**，不是对象 —— 这点和 webhook 不一样。

    好处：一个应用可以被拉进任意多个群，不用每换一个群就改配置。
    """

    name = "feishu-app"
    BASE = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id, app_secret, chat_id, roles=None, keyword=None,
                 text_limit=20000):
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self.roles = roles or {}
        self.keyword = keyword
        self.text_limit = text_limit
        self._token = None
        self._expire_at = 0.0

    # ---- 底层 HTTP ----
    def _request(self, method, url, body=None, headers=None):
        operation = ('feishu.auth' if '/tenant_access_token/' in url else
                     'feishu.send' if method == 'POST' else 'feishu.query')
        return diagnostics.request(operation, lambda: self._perform_request(method, url, body, headers))

    def _perform_request(self, method, url, body=None, headers=None):
        hat = {"Content-Type": "application/json; charset=utf-8"}
        hat.update(headers or {})
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=hat)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # 400/403 等是确定性业务错误；取飞书返回的 code/msg，不交给 _send 重试。
            try:
                detail = json.loads(exc.read(4096).decode("utf-8"))
            except (ValueError, UnicodeError):
                detail = {}
            operation = "获取访问令牌" if "/tenant_access_token/" in url else "发送或查询消息"
            raise DeliveryError("飞书%s失败：HTTP %s，code=%s" %
                                (operation, exc.code, diagnostics.code_label(detail.get('code'))),
                                transient=exc.code == 429 or 500 <= exc.code < 600 or detail.get("code") == 99991400,
                                api_code=detail.get('code'), http_status=exc.code) from None
        payload = json.loads(text) if text else {}
        code = payload.get("code")
        if code not in (0, None):
            raise DeliveryError("飞书应用接口错误 code=%s" % diagnostics.code_label(code), transient=code == 99991400, api_code=code)
        return payload

    def _tenant_token(self):
        if self._token and time.time() < self._expire_at - 60:
            return self._token
        payload = self._request(
            "POST", self.BASE + "/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret})
        self._token = payload.get("tenant_access_token")
        self._expire_at = time.time() + int(payload.get("expire", 7200))
        if not self._token:
            raise RuntimeError("拿不到 tenant_access_token，检查 app_id / app_secret")
        return self._token

    def _send(self, msg_type, content_obj, request_uuid=None):
        last_error = None
        for attempt in range(RETRY_TIMES):
            try:
                token = self._tenant_token()
                body = {
                    "receive_id": self.chat_id,
                    "msg_type": msg_type,
                    # im/v1 要求 content 是字符串
                    "content": json.dumps(content_obj, ensure_ascii=False),
                }
                if request_uuid:
                    body["uuid"] = request_uuid
                return self._request(
                    "POST", self.BASE + "/im/v1/messages?receive_id_type=chat_id",
                    body, {"Authorization": "Bearer " + token})
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == RETRY_TIMES - 1:
                    raise RuntimeError("推送失败（已重试 %d 次）：%s" % (RETRY_TIMES, exc))
                time.sleep(2 ** attempt)
        raise RuntimeError(str(last_error))

    # ---- 对外 ----
    def _targets(self, at_roles):
        ids = []
        for role in at_roles or []:
            ids.extend(self.roles.get(role, []))
        return ids

    def _with_keyword(self, text):
        if self.keyword and self.keyword not in text:
            return "【%s】\n%s" % (self.keyword, text)
        return text

    def send_text(self, content, at_roles=None):
        text = self._with_keyword(truncate_bytes(content, self.text_limit))
        ids = self._targets(at_roles)
        if ids:
            text += "\n" + " ".join(FeishuNotifier._at_tag(i) for i in ids)
        return self._send("text", {"text": text})

    def reply_text(self, message_id, content, request_uuid=None):
        """在消息原群原线程回复，避免发到配置中的默认 chat_id。"""
        if not message_id:
            raise ValueError("回复缺少 message_id")
        text = self._with_keyword(truncate_bytes(content, self.text_limit))
        body = {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
        if request_uuid:
            body["uuid"] = request_uuid
        return self._request(
            "POST", self.BASE + "/im/v1/messages/%s/reply" % quote(message_id, safe=""),
            body, {"Authorization": "Bearer " + self._tenant_token()})

    def get_message(self, message_id):
        return self._request('GET', self.BASE + '/im/v1/messages/' + quote(message_id, safe=''),
                             headers={'Authorization': 'Bearer ' + self._tenant_token()}).get('data', {}).get('items', [])

    def recent_messages(self, chat_id, end_time):
        params = {'container_id_type': 'chat', 'container_id': chat_id,
                  'sort_type': 'ByCreateTimeDesc', 'page_size': 50, 'end_time': str(end_time)}
        result = self._request('GET', self.BASE + '/im/v1/messages?' + urlencode(params),
                               headers={'Authorization': 'Bearer ' + self._tenant_token()})
        return result.get('data', {})

    def reply_image(self, message_id, image_key, request_uuid=None):
        """Reply with an uploaded image in the original conversation."""
        if not message_id or not image_key:
            raise ValueError('图片回复缺少消息或图片标识')
        body = {'msg_type': 'image', 'content': json.dumps({'image_key': image_key})}
        if request_uuid:
            body['uuid'] = request_uuid
        return self._request('POST', self.BASE + '/im/v1/messages/%s/reply' % quote(message_id, safe=''),
                             body, {'Authorization': 'Bearer ' + self._tenant_token()})

    def reply_card(self, message_id, card, request_uuid=None):
        """Reply using a native Feishu card in the originating conversation."""
        if not message_id:
            raise ValueError("回复缺少 message_id")
        body = {"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)}
        if request_uuid:
            body['uuid'] = request_uuid
        return self._request(
            "POST", self.BASE + "/im/v1/messages/%s/reply" % quote(message_id, safe=""),
            body, {"Authorization": "Bearer " + self._tenant_token()})

    def reply_markdown(self, message_id, content):
        """Compatibility entry: convert content to native fields and text before sending."""
        from .card_view import answer_card
        text = self._with_keyword(truncate_bytes(content, self.text_limit))
        return self.reply_card(message_id, answer_card(text))

    def send_markdown(self, content):
        text = self._with_keyword(_strip_blockquote(truncate_bytes(content, self.text_limit)))
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
        }
        return self._send("interactive", card)

    def send_card(self, card, request_uuid=None):
        """通过 im/v1/messages 发送结构化卡片，content 由 _send 序列化。"""
        return self._send("interactive", card, request_uuid=request_uuid)


def feishu_allowed_chat_ids(env):
    """将 .env 中的群白名单解析成独立 chat_id，兼容中英文分隔符。"""
    raw = (env.get("FEISHU_ALLOWED_CHAT_IDS") or "").strip()
    return [x for x in re.split(r"[,，;；\s]+", raw) if x]


def build_notifier(cfg, env):
    """有 webhook 就用真渠道，没有就降级为控制台。

    渠道由 config/rules.json 的 notify.channel 决定：wecom（默认）或 feishu。
    """
    from .settings import strip_comments

    notify_cfg = cfg.get("notify", {})
    roles = strip_comments(notify_cfg.get("roles", {}))
    channel = (notify_cfg.get("channel") or "wecom").strip().lower()

    if channel in ("feishu_app", "feishu-app"):
        app_id = (env.get("FEISHU_APP_ID") or "").strip()
        app_secret = (env.get("FEISHU_APP_SECRET") or "").strip()
        chat_id = (env.get("FEISHU_CHAT_ID") or "").strip()
        if chat_id:
            parsed = feishu_allowed_chat_ids({"FEISHU_ALLOWED_CHAT_IDS": chat_id})
            if len(parsed) != 1 or parsed[0] != chat_id:
                raise NotifierConfigError("FEISHU_CHAT_ID 只能填写一个群 ID；多个可查询群请放在 FEISHU_ALLOWED_CHAT_IDS")
        if not chat_id:
            allowed = feishu_allowed_chat_ids(env)
            if len(allowed) == 1:
                chat_id = allowed[0]
        if not (app_id and app_secret and chat_id):
            return ConsoleNotifier()
        return FeishuAppNotifier(
            app_id, app_secret, chat_id,
            roles=roles,
            keyword=(env.get("FEISHU_KEYWORD") or "").strip() or None,
        )

    if channel == "feishu":
        webhook = (env.get("FEISHU_WEBHOOK") or "").strip()
        if not webhook:
            return ConsoleNotifier()
        return FeishuNotifier(
            webhook,
            roles=roles,
            secret=(env.get("FEISHU_SECRET") or "").strip() or None,
            keyword=(env.get("FEISHU_KEYWORD") or "").strip() or None,
        )

    if channel == "dingtalk":
        # 预留位置：钉钉与企业微信结构很像，需要加签名计算
        raise NotImplementedError("钉钉渠道暂未实现（需要加 timestamp+sign）")

    webhook = (env.get("WECOM_WEBHOOK") or "").strip()
    if not webhook:
        return ConsoleNotifier()

    return WeComNotifier(
        webhook,
        roles=roles,
        md_limit=int(notify_cfg.get("markdown_max_bytes", 4096)),
        text_limit=int(notify_cfg.get("text_max_bytes", 2048)),
    )
