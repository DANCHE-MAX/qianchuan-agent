"""Small, allowlisted stdout evidence. Never serialize requests or exception text."""
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import math
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from .reliability import network_error

CHINA = timezone(timedelta(hours=8))
_LOCK = threading.Lock()
_EVENTS = {'task_start', 'task_success', 'task_failure', 'api_success', 'api_failure',
           'scheduler_gap', 'clock_regression', 'connection_interrupted',
           'connection_restored', 'reaction_ignored', 'request_attempt'}
_TASKS = {'半小时监测', '小时采集', '小时报表', '状态列表', '授权维护', '重点日报', '消息处理进度'}
_OPERATIONS = {'qianchuan.request', 'qianchuan.refresh', 'feishu.auth', 'feishu.send', 'feishu.query'}
# Fixed allowlisted call identifiers; never URLs, params, headers or tokens.
_APIS = {'qianchuan.hourly_report', 'qianchuan.plan_list', 'qianchuan.snapshot', 'qianchuan.daily_report', 'qianchuan.plan_detail'}
_API = ContextVar('diagnostics_api', default=None)


@contextmanager
def api_label(name):
    """Tag API events in this context with an allowlisted call identifier."""
    token = _API.set(name if name in _APIS else None)
    try:
        yield
    finally:
        _API.reset(token)


def numeric_code(value):
    return value if type(value) is int and 0 <= value <= 2147483647 else None


def code_label(value):
    safe = numeric_code(value)
    return str(safe) if safe is not None else '未知'


def emit(event, **fields):
    """Best-effort logging must not turn a completed operation into a failure."""
    if event not in _EVENTS:
        return
    record = {'time': datetime.now(CHINA).isoformat(timespec='milliseconds'), 'event': event}
    if fields.get('task') in _TASKS:
        record['task'] = fields['task']
    if fields.get('operation') in _OPERATIONS:
        record['operation'] = fields['operation']
    api = fields.get('api', _API.get())
    if api in _APIS and (str(record.get('event', '')).startswith('api_') or record.get('event')=='request_attempt'):
        record['api'] = api
    for key in ('api_code', 'http_status'):
        value = numeric_code(fields.get(key))
        if value is not None:
            record[key] = value
    for key in ('elapsed_seconds', 'gap_seconds', 'threshold_seconds'):
        value = fields.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            record[key] = round(value, 3)
    error_type = fields.get('error_type')
    if isinstance(error_type, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,79}', error_type):
        record['error_type'] = error_type
    if fields.get('network_error') in {'dns','timeout','tls','connection_refused','connection','network'}:
        record['network_error']=fields['network_error']
    if type(fields.get('attempt')) is int and 1<=fields['attempt']<=10:
        record['attempt']=fields['attempt']
    previous = fields.get('previous_time')
    if isinstance(previous, datetime) and previous.tzinfo is not None:
        record['previous_time'] = previous.astimezone(CHINA).isoformat(timespec='milliseconds')
    try:
        with _LOCK:
            print(json.dumps(record, ensure_ascii=False, separators=(',', ':')), flush=True)
    except (OSError, UnicodeError, ValueError):
        pass


def request(operation, action):
    """Observe an API attempt without changing its result, retries or exception."""
    started = time.monotonic()
    try:
        result = action()
    except Exception as exc:
        emit('api_failure', operation=operation, elapsed_seconds=time.monotonic()-started,
             error_type=type(exc).__name__, network_error=network_error(exc), api_code=getattr(exc, 'api_code', None),
             http_status=getattr(exc, 'http_status', None))
        raise
    code = result.get('code') if isinstance(result, dict) else None
    success = isinstance(result, dict) and code in (None, 0)
    if operation in ('qianchuan.request', 'qianchuan.refresh'):
        success = isinstance(result, dict) and type(code) is int and code == 0
    emit('api_success' if success else 'api_failure', operation=operation,
         elapsed_seconds=time.monotonic()-started, api_code=code)
    return result


def measure(operation):
    def decorate(action):
        @wraps(action)
        def wrapped(*args, **kwargs):
            return request(operation, lambda: action(*args, **kwargs))
        return wrapped
    return decorate


class GapTracker:
    """Observe wall-clock gaps; they do not prove a freeze or any single cause."""
    def __init__(self, threshold_seconds=180):
        self.threshold_seconds = threshold_seconds
        self.previous = None

    def observe(self, now=None):
        now = now or datetime.now(CHINA)
        if now.tzinfo is None:
            raise ValueError('调度观察时间须带时区')
        previous, self.previous = self.previous, now
        if previous is None:
            return
        gap = (now-previous).total_seconds()
        if gap < 0:
            emit('clock_regression', previous_time=previous)
        elif gap > self.threshold_seconds:
            emit('scheduler_gap', gap_seconds=gap, threshold_seconds=self.threshold_seconds,
                 previous_time=previous)
