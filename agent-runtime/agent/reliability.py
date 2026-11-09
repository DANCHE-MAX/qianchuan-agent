"""Bounded retries and OS locks shared by the CLI and listener."""
import os
import random
import sqlite3
import time
import urllib.error
from contextlib import contextmanager
from pathlib import Path


class ServiceError(ValueError):
    def __init__(self, message, transient=False, api_code=None, http_status=None):
        super().__init__(message)
        self.transient = transient
        self.api_code, self.http_status = api_code, http_status


class DeliveryError(RuntimeError):
    def __init__(self, message, transient=False, api_code=None, http_status=None):
        super().__init__(message)
        self.transient = transient
        self.api_code, self.http_status = api_code, http_status


class BusyError(OSError):
    transient = True


def retryable(exc):
    if hasattr(exc, 'transient'):
        return exc.transient
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    if isinstance(exc, sqlite3.OperationalError):
        return any(word in str(exc).lower() for word in ('locked', 'busy'))
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))


def retry_io(operation, attempts=3, sleep=time.sleep):
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if attempt == attempts - 1 or not retryable(exc):
                raise
            sleep(min(8, 2 ** attempt) + random.uniform(0, 0.25))


@contextmanager
def file_lock(path, timeout=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open('a+b')
    locked = False
    deadline = time.monotonic() + timeout
    try:
        if path.stat().st_size == 0:
            f.write(b'0')
            f.flush()
        while True:
            try:
                f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise BusyError('已有进程正在执行此操作，请稍后重试。') from None
                time.sleep(0.1)
        yield
    finally:
        if locked:
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def network_error(exc):
    """Safe error class only; never log exception text/URLs or local addresses."""
    import socket
    import ssl
    if isinstance(exc, urllib.error.HTTPError):return None
    reason=getattr(exc,'reason',exc)
    if isinstance(reason,socket.gaierror):return 'dns'
    if isinstance(reason,(TimeoutError,socket.timeout)):return 'timeout'
    if isinstance(reason,ssl.SSLError):return 'tls'
    if isinstance(reason,ConnectionRefusedError):return 'connection_refused'
    if isinstance(reason,ConnectionError):return 'connection'
    if isinstance(exc,urllib.error.URLError):return 'network'
    return getattr(exc,'network_error',None)
