"""Read-only live account balance; API amounts are 1/1000 fen, not yuan."""
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

from .card_view import card, fields, text_element
from .notify import feishu_allowed_chat_ids
from .conversation import Conversation
from . import qc_auth
from .reliability import ServiceError

ENDPOINT = 'https://api.oceanengine.com/open_api/v1.0/qianchuan/account/balance/get/'


def matches(command):
    text = re.sub(r'\s+', '', command).strip('。！？!?')
    return re.fullmatch(
        r'(?:请)?(?:帮我|为我)?(?:查询|查看|查一下|查)?(?:当前)?(?:千川)?(?:账户|账号)?(?:的)?余额(?:是多少|为多少|有多少|还剩多少)?',
        text) is not None


def _get(account, token):
    req = urllib.request.Request(ENDPOINT + '?' + urllib.parse.urlencode(
        {'advertiser_id': account}), headers={'Access-Token': token})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def fetch(env, request=None):
    account = env.get('QC_ADVERTISER_IDS', '').strip()
    token = env.get('QC_ACCESS_TOKEN', '').strip()
    if not account.isascii() or not account.isdigit() or not token:
        raise ValueError('请管理员配置一个有效的千川账户及访问令牌后重试。')
    try:
        payload = qc_auth.call(env, lambda current: (request or _get)(account, current))
        if not isinstance(payload, dict) or payload.get('code') != 0:
            raise ValueError()
        data = payload['data']
        if str(data['advertiser_id']) != account:
            raise ValueError()
        amounts = {}
        for key in ('account_total', 'account_valid', 'account_frozen'):
            amount = Decimal(str(data[key]))
            if not amount.is_finite() or amount != amount.to_integral_value():
                raise ValueError()
            amounts[key] = str(amount / Decimal('100000'))
    except ServiceError:
        raise
    except Exception:
        # Never expose tokens, upstream bodies or exception details in chat.
        raise ValueError('余额查询失败或超时，请稍后重试；如持续失败，请管理员检查千川授权、令牌和账户权限。') from None
    return {'kind': 'current_account_balance', 'advertiser_id': account,
            'fetched_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='microseconds'),
            'currency': 'CNY', 'amounts_yuan': amounts}


def describe(snapshot):
    stamp = datetime.fromisoformat(snapshot['fetched_at']).strftime('%Y-%m-%d %H:%M:%S')
    amounts = snapshot['amounts_yuan']
    pairs = [(label, format(Decimal(amounts[key]), ',.2f') + ' 元') for label, key in (
        ('可用余额', 'account_valid'), ('总余额', 'account_total'), ('冻结/不可用余额', 'account_frozen'))]
    scope = '千川账户：%s\n查询时间：%s（北京时间）' % (snapshot['advertiser_id'], stamp)
    note = '本次查询的当前余额，金额已换算为元；会随消耗和充值变化。'
    rendered = card('千川账户｜当前余额', [text_element(scope), fields(pairs), text_element(note)])
    return rendered, scope + '\n' + '\n'.join(k + '：' + v for k, v in pairs) + '\n' + note


def reply(notifier, message_id, command, root, env, context, memory=None):
    chat = context.get('chat_id')
    allowed = set(feishu_allowed_chat_ids(env) or [env.get('FEISHU_CHAT_ID')])
    if not chat or chat not in allowed or not message_id:
        return None
    try:
        snapshot = fetch(env)
    except ValueError as exc:
        return notifier.reply_card(message_id, card('千川账户｜余额查询失败', [text_element(str(exc))]))
    directory = Path(root) / 'data' / 'qianchuan'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r'[^0-9]', '', snapshot['fetched_at'])
    path = directory / ('account_balance_%s_%s.json' % (snapshot['advertiser_id'], stamp))
    with path.open('x', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    rendered, description = describe(snapshot)
    result = notifier.reply_card(message_id, rendered)
    memory = memory or Conversation(Path(root) / 'data' / 'conversation.db', account=env.get('QC_ADVERTISER_IDS'))
    memory.save(chat, context.get('sender_id'), [{
        'question': command[:2000], 'answer': description,
        'source': json.dumps(snapshot, ensure_ascii=False),
        'report_snapshot': None, 'display_kind': 'answer'}])
    return result
