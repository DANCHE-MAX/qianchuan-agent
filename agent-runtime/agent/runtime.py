"""Supervise the message worker while the SDK owns its reconnect lifecycle."""
import threading
import time


class ListenerSupervisor:
    def __init__(self, process_one, maintenance, interval=10):
        self.process_one = process_one
        self.maintenance = maintenance
        self.interval = interval
        self.stopping = threading.Event()
        self.worker = None
        self.supervisor = None
        self.last_tick = 0
        self.last_maintenance = 0
        self.last_problem = None

    def work(self):
        while not self.stopping.is_set():
            self.last_tick = time.monotonic()
            try:
                if not self.process_one():
                    self.stopping.wait(1)
            except Exception as exc:
                # Includes failures reading/writing SQLite, outside handler processing.
                print('消息队列暂不可用，将重试（%s）。' % type(exc).__name__, flush=True)
                self.stopping.wait(5)

    def ensure_worker(self):
        if not self.stopping.is_set() and (self.worker is None or not self.worker.is_alive()):
            self.worker = threading.Thread(target=self.work, name='feishu-reply-worker', daemon=True)
            self.worker.start()

    def supervise(self):
        while not self.stopping.is_set():
            self.ensure_worker()
            if time.monotonic() - self.last_maintenance >= 60:
                try:
                    self.maintenance()
                    if self.last_problem:
                        print('千川授权检查已恢复。', flush=True)
                    self.last_problem = None
                except Exception as exc:
                    from .reliability import ServiceError
                    problem = str(exc) if isinstance(exc, ServiceError) else type(exc).__name__
                    if problem != self.last_problem:
                        print('千川授权检查：%s' % problem, flush=True)
                    self.last_problem = problem
                self.last_maintenance = time.monotonic()
            self.stopping.wait(self.interval)

    def start(self):
        self.ensure_worker()
        self.supervisor = threading.Thread(target=self.supervise, name='feishu-supervisor', daemon=True)
        self.supervisor.start()

    def stop(self):
        self.stopping.set()
        # Bounded network operations may still be finishing. Never start a replacement
        # worker while one is merely busy; process termination remains the OS's job.
        for thread in (self.worker, self.supervisor):
            if thread:
                thread.join(timeout=2)
