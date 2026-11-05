"""Qianchuan credentials: atomic rotation, cross-process lock, bounded API retries.

Official docs: 9000000000000001 (refresh), 9000000000000002 (codes).
Only an env loaded with settings.load_env has a persistent credential location.
"""
import json
import math
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from pathlib import Path

from .reliability import ServiceError, file_lock, retry_io
from . import diagnostics

REFRESH_URL = 'https://ad.oceanengine.com/open_api/oauth2/refresh_token/'
TOKEN_KEYS = ('QC_ACCESS_TOKEN', 'QC_REFRESH_TOKEN', 'QC_ACCESS_TOKEN_EXPIRES_AT',
              'QC_REFRESH_TOKEN_EXPIRES_AT')
TRANSIENT_CODES = {40100, 40110, 50000}
_managers = {}
_registry_lock = threading.Lock()


@diagnostics.measure('qianchuan.refresh')
def _refresh_request(env):
    body = {'app_id': int(env['QC_APP_ID']), 'secret': env['QC_APP_SECRET'],
            'grant_type': 'refresh_token', 'refresh_token': env['QC_REFRESH_TOKEN']}
    req = urllib.request.Request(REFRESH_URL, data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(8192))
        except (ValueError, UnicodeError):
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get('code'), int) and payload['code'] != 0:
            return payload
        raise ServiceError('千川授权刷新请求失败（HTTP %s）。' % exc.code,
                           transient=exc.code == 429 or 500 <= exc.code < 600, http_status=exc.code) from None


def _atomic_write(path, text):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class TokenManager:
    def __init__(self, env, clock=time.time, request=None):
        self.env = env
        self.path = Path(env['_QC_ENV_PATH']) if env.get('_QC_ENV_PATH') else None
        self.clock = clock
        self.request = request or _refresh_request
        self.lock = threading.RLock()
        self.cooldown_until = 0
        self.last_error = None
        self.last_refresh_token = None

    def _read(self):
        if self.path:
            from .settings import load_env
            return load_env(self.path)
        return dict(self.env)

    def _commit(self, values):
        if self.path:
            lines = self.path.read_text(encoding='utf-8-sig').splitlines()
            lines = [line for line in lines if line.partition('=')[0].strip() not in TOKEN_KEYS]
            lines.extend(key + '=' + values[key] for key in TOKEN_KEYS)
            _atomic_write(self.path, '\n'.join(lines) + '\n')
        self.env.update(values)

    def _recover(self):
        if not self.path:
            return
        pending = self.path.parent / '.qc-token.pending'
        if pending.exists():
            data = json.loads(pending.read_text(encoding='utf-8'))
            current = self._read()
            # A manual reauthorization takes precedence over an old pending rotation.
            if current.get('QC_REFRESH_TOKEN') in (data['previous_refresh'], data['values']['QC_REFRESH_TOKEN']):
                self._commit(data['values'])
            pending.unlink()

    def token(self, rejected=None, force=False):
        with self.lock:
            guard = file_lock(self.path.parent / '.qc-token.lock', timeout=30) if self.path else nullcontext()
            with guard:
                try:
                    self._recover()
                    env = self._read()
                    token = env.get('QC_ACCESS_TOKEN', '').strip()
                    expiry = float(env.get('QC_ACCESS_TOKEN_EXPIRES_AT') or 0)
                    due = expiry > 0 and self.clock() >= expiry - 300
                    if not force and token and not due and (not rejected or rejected != token):
                        return token
                    # A concurrent caller may already have refreshed the rejected token.
                    if rejected and token != rejected and token and not due:
                        return token
                    refresh = env.get('QC_REFRESH_TOKEN', '').strip()
                    if self.last_error and refresh == self.last_refresh_token and self.clock() < self.cooldown_until:
                        raise self.last_error
                    if not refresh or not env.get('QC_APP_ID', '').isdigit() or not env.get('QC_APP_SECRET'):
                        raise ServiceError('千川授权配置不完整，请管理员配置应用凭据和刷新令牌。')
                    refresh_expiry = float(env.get('QC_REFRESH_TOKEN_EXPIRES_AT') or 0)
                    if refresh_expiry and self.clock() >= refresh_expiry:
                        raise ServiceError('千川刷新令牌已到期，请管理员重新授权。')
                    # A transport timeout has an uncertain rotation outcome. Do not blindly loop.
                    payload = self.request(env)
                    code = payload.get('code') if isinstance(payload, dict) else None
                    if code != 0:
                        if code == 40002:
                            raise ServiceError('千川令牌刷新被拒绝：应用App ID与App Secret不匹配或无操作权限，请管理员核对同一应用的凭据（code=40002）。')
                        if code in (40103, 40107):
                            raise ServiceError('千川刷新令牌已失效，请管理员重新授权并更新本机凭据。')
                        raise ServiceError('千川授权刷新失败，请管理员检查应用凭据和授权状态（code=%s）。' % diagnostics.code_label(code),
                                           transient=code in TRANSIENT_CODES, api_code=code)
                    data = payload['data']
                    values = {}
                    for key, source in [('QC_ACCESS_TOKEN', 'access_token'), ('QC_REFRESH_TOKEN', 'refresh_token')]:
                        value = data[source]
                        if not isinstance(value, str) or not value or any(c.isspace() for c in value):
                            raise ValueError()
                        values[key] = value
                    for key, source in [('QC_ACCESS_TOKEN_EXPIRES_AT', 'expires_in'),
                                        ('QC_REFRESH_TOKEN_EXPIRES_AT', 'refresh_token_expires_in')]:
                        seconds = float(data[source])
                        if not math.isfinite(seconds) or seconds <= 300:
                            raise ValueError()
                        values[key] = str(self.clock() + seconds)
                    if self.path:
                        pending = self.path.parent / '.qc-token.pending'
                        _atomic_write(pending, json.dumps({'previous_refresh': refresh, 'values': values}))
                    self._commit(values)
                    if self.path:
                        pending.unlink()
                    self.last_error = None
                    self.cooldown_until = 0
                    return values['QC_ACCESS_TOKEN']
                except ServiceError as exc:
                    error = exc
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    error = ServiceError('千川授权刷新暂未确认成功，请稍后重试；未输出任何令牌。', transient=True)
                except Exception:
                    error = ServiceError('千川令牌更新或保存失败，请管理员检查配置文件和磁盘；未使用过期令牌继续查询。')
                self.last_error = error
                self.last_refresh_token = self._read().get('QC_REFRESH_TOKEN')
                self.cooldown_until = self.clock() + (60 if error.transient else 300)
                raise error from None


def manager(env):
    key = str(Path(env['_QC_ENV_PATH']).resolve()) if env.get('_QC_ENV_PATH') else None
    if key is None:
        return TokenManager(env)
    with _registry_lock:
        if key not in _managers:
            _managers[key] = TokenManager(env)
        return _managers[key]


@diagnostics.measure('qianchuan.request')
def call(env, operation):
    tokens = manager(env)
    token = tokens.token()

    def invoke(value):
        def once():
            payload = operation(value)
            if not isinstance(payload, dict):
                raise ServiceError('千川接口返回格式异常，请稍后重试。')
            if payload.get('code') in TRANSIENT_CODES:
                raise ServiceError('千川接口繁忙或限流，请稍后重试。', transient=True, api_code=payload['code'])
            return payload
        try:
            return retry_io(once)
        except ServiceError:
            raise
        except urllib.error.HTTPError as exc:
            raise ServiceError('千川接口请求失败（HTTP %s），请检查权限或稍后重试。' % exc.code,
                               transient=exc.code == 429 or 500 <= exc.code < 600, http_status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            raise ServiceError('千川网络请求超时或中断，请稍后重试。', transient=True) from None

    payload = invoke(token)
    if payload.get('code') == 40102:
        payload = invoke(tokens.token(rejected=token))
    code = payload.get('code')
    if code != 0:
        messages = {40102: '千川访问令牌仍然失效，请管理员检查授权。',
                    40002: '千川账户或接口权限不足，请管理员检查授权及应用凭据。',
                    40104: '千川访问令牌无效，请管理员检查配置或重新授权。'}
        raise ServiceError(messages.get(diagnostics.numeric_code(code), '千川接口校验失败（code=%s），请检查请求参数和权限。' % diagnostics.code_label(code)), api_code=code)
    return payload


def maintain(env):
    """Called by listener supervisor; no refresh until an actual expiry is known."""
    if env.get('QC_ACCESS_TOKEN'):
        return manager(env).token()
