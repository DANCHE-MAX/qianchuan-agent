"""Local cross-process pacing and shared cooldown; not a claimed platform quota."""
import hashlib
import json
import random
import time
from pathlib import Path
from .reliability import file_lock, network_error


def run(env, operation, *, clock=time.time, sleep=time.sleep, jitter=lambda:random.uniform(0,1)):
    source=env.get('_QC_ENV_PATH')
    if not source:
        return operation()  # Isolated tests and clients without a persistent runtime.
    directory=Path(source).resolve().parent.parent/'data'/'qianchuan'/'request_gate'
    directory.mkdir(parents=True,exist_ok=True)
    scope=hashlib.sha256(str(env.get('QC_APP_ID','local')).encode()).hexdigest()[:20]
    state=directory/(scope+'.json')
    with file_lock(directory/(scope+'.lock'),timeout=120):
        previous=json.loads(state.read_text()) if state.exists() else {}
        wait=max(0,float(previous.get('next',0))-clock())
        if wait:sleep(wait)
        def save(delay,failures):
            tmp=state.with_suffix('.tmp');tmp.write_text(json.dumps({'next':clock()+delay,'failures':failures}));tmp.replace(state)
        try:
            value=operation()
        except Exception as exc:
            category=network_error(exc)
            if category or getattr(exc,'code',None)==429:
                failures=min(int(previous.get('failures',0))+1,4)
                save(min(30,5*2**(failures-1))+jitter(),failures)
            else:save(0.8,0)
            raise
        if isinstance(value,dict) and value.get('code') in (40100,40110,50000):
            failures=min(int(previous.get('failures',0))+1,5)
            save(min(60,5*2**(failures-1))+jitter(),failures)
        else:save(0.8,0)
        return value
