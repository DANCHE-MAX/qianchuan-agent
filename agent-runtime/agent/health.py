"""Persistent local watchdog evidence and incident/recovery notifications.

An OS-scheduled independent invocation is required to detect process termination.
No health success implies that a WebSocket is connected or an API is authorized.
"""
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager, closing
from datetime import datetime, timedelta
from pathlib import Path
from . import hourly as h, data_quality, settings, diagnostics
from .card_view import card, text_element
from .notify import feishu_allowed_chat_ids
from .reliability import file_lock

DEFAULTS = {'enabled': True, 'heartbeat_minutes': 3, 'task_timeout_minutes': 15,
            'incident_delay_minutes': 3, 'startup_grace_minutes': 5,
            'snapshot_stale_minutes': 45, 'daily_late_minutes': 15, 'at_roles': ['运维']}


def options(root):
    path = Path(root)/'config/health.json'
    cfg = dict(DEFAULTS, **(json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}))
    if type(cfg['enabled']) is not bool: raise ValueError('健康检查启用配置无效')
    for key in DEFAULTS:
        if key.endswith('_minutes') and (type(cfg[key]) is not int or not 1 <= cfg[key] <= 1440):
            raise ValueError('健康检查阈值无效')
    if not isinstance(cfg['at_roles'], list): raise ValueError('健康检查@配置无效')
    return cfg


class Store:
    def __init__(self, root): self.path = Path(root)/'data/qianchuan/health.db'

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS signals(account TEXT,name TEXT,stamp TEXT,state TEXT,detail TEXT,PRIMARY KEY(account,name))')
            db.execute('CREATE TABLE IF NOT EXISTS incidents(account TEXT,code TEXT,since TEXT,active INTEGER DEFAULT 0,PRIMARY KEY(account,code))')
            db.execute('CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY,account TEXT,chat TEXT,payload TEXT,sent INTEGER DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS notice_context(id TEXT PRIMARY KEY,code TEXT,kind TEXT,scope TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS incident_scopes(account TEXT,code TEXT,scope TEXT,PRIMARY KEY(account,code))')
            db.execute('CREATE TABLE IF NOT EXISTS advisories(account TEXT,day TEXT,kind TEXT,PRIMARY KEY(account,day,kind))')
            db.execute('CREATE TABLE IF NOT EXISTS assessment_clock(account TEXT PRIMARY KEY,stamp TEXT)')
            with db: yield db
        finally: db.close()

    def put(self, account, name, state, detail='', now=None):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO signals VALUES(?,?,?,?,?)',
                       (account, name, (now or h.now_china()).isoformat(), state, detail))

    def signals(self, account):
        with self.db() as db:
            return {name: {'stamp': stamp, 'state': state, 'detail': detail}
                    for name, stamp, state, detail in db.execute('SELECT name,stamp,state,detail FROM signals WHERE account=?', (account,))}


def record(root, env, name, state, detail=''):
    """Sub-result signal (e.g. 小时报表/状态列表) with its own stamp; class names only."""
    if state not in ('ok', 'failed'): raise ValueError('子任务状态无效')
    detail = detail if isinstance(detail, str) and detail.isidentifier() else ''
    store = Store(root); account = h.account_id(env)
    previous = store.signals(account).get(name)
    store.put(account, name, state, detail)
    if previous and (previous['state'], previous['detail']) == (state, detail):
        return
    diagnostics.emit('task_success' if state == 'ok' else 'task_failure', task=name, error_type=detail or None)


def subresults(root, account, signals):
    """Separate success/failure and data update times for hourly reports vs plan status list."""
    out = {}
    try:
        store = h.Store(root)
        latest_hour = latest_fetched = None
        if store.path.exists():
            with store.connect() as db:
                rows = db.execute('SELECT start, payload FROM hours WHERE account=?', (account,)).fetchall()
            for start, raw in rows:
                # Max of validated fetched_at: back-filled older hours count as updates too.
                try:
                    value = json.loads(raw); stamp = data_quality.stamp(value['fetched_at'])
                    begin = data_quality.stamp(start)
                    if (value.get('start') != start or str(value.get('advertiser_id')) != account
                            or begin != h.floor_hour(begin) or stamp < begin + timedelta(hours=1)
                            or not isinstance(value.get('rows'), list)): continue
                except (ValueError, KeyError, TypeError):
                    continue
                if latest_fetched is None or stamp > latest_fetched: latest_fetched = stamp
                if latest_hour is None or start > latest_hour: latest_hour = start
            plans = store.plans(account)
        else:
            plans = None
        latest = {'fetched_at': latest_fetched.isoformat()} if latest_fetched else None
        for name, data_at in (('小时报表', latest and latest.get('fetched_at')), ('状态列表', plans and plans.get('fetched_at'))):
            sig = signals.get(name) or {}
            out[name] = {'state': sig.get('state', 'unknown'), 'checked_at': sig.get('stamp'),
                         'error_type': sig.get('detail') or None, 'data_updated_at': data_at}
        out['小时报表']['latest_hour'] = latest_hour
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        out = {'error': '子结果读取失败'}
    return out


def run(root, env, name, action):
    """Record only exception class names; never store exception messages or tokens."""
    store = Store(root); account = h.account_id(env)
    started = time.monotonic()
    diagnostics.emit('task_start', task=name)
    # A retry must not briefly erase an unresolved failure before it succeeds.
    previous = store.signals(account).get(name)
    if not previous or previous['state'] != 'failed':
        store.put(account, name, 'running')
    try:
        result = action()
    except Exception as exc:
        store.put(account, name, 'failed', type(exc).__name__)
        diagnostics.emit('task_failure', task=name, elapsed_seconds=time.monotonic()-started,
                         error_type=type(exc).__name__)
        raise
    store.put(account, name, 'ok')
    diagnostics.emit('task_success', task=name, elapsed_seconds=time.monotonic()-started)
    return result


def inspect(root, env, now=None):
    from . import daily_brief, monitor, collection_state
    root=Path(root);now=now or h.now_china();cfg=options(root)
    if now.tzinfo is None:raise ValueError('健康巡检时间须带时区')
    now=now.astimezone(h.CHINA);account=h.account_id(env);day=now.date().isoformat()
    signals=Store(root).signals(account);issues={};verified=set();deferred={}
    def read(code,label,action,default):
        verified.add(code)
        try:return action()
        except (OSError,ValueError,sqlite3.Error,KeyError,TypeError,ArithmeticError):
            issues[code]=label+'读取或校验失败；未以0或旧数据代替，请检查数据库和文件权限。'
            return default
    def age(value):return (now-data_quality.stamp(value)).total_seconds()/60
    verified.add('listener');heartbeat=signals.get('listener')
    if not heartbeat or age(heartbeat['stamp'])>cfg['heartbeat_minutes']:
        issues['listener']='监听进程心跳缺失或超过阈值；请检查原服务是否退出或卡死。'
    started=signals.get('started')
    grace=bool(started and 0<=age(started['stamp'])<cfg['startup_grace_minutes'])
    for name,value in signals.items():
        if name in ('listener','started'):continue
        code='task:'+name;verified.add(code)
        if name=='状态列表' and value['state']=='failed':issues[code]='计划状态列表刷新失败（%s）；小时报表单独核对，不受此影响。'%value['detail']
        elif value['state']=='failed':issues[code]='%s任务失败（%s），请检查安全日志。'%(name,value['detail'])
        elif age(value['stamp'])>cfg['task_timeout_minutes']:issues[code]=name+'任务长时间没有完成或更新。'
    config=settings.load_config(root/'config/rules.json');monitoring=bool(config.get('monitoring',{}).get('enabled',False))
    active_kinds=settings.collection_kinds(config,account)
    for kind in ('plan','material'):
        if kind not in active_kinds:verified.update(('snapshot:'+kind,'read:'+kind,'time:'+kind))
    windows=read('coverage_read','采集启用记录',lambda:collection_state.sync(root,account,now,monitoring),None)
    fallback={'account':account,'date':day,'covered_hours':0,'expected_hours':now.hour,'missing_hours':['无法核对'],
              'invalid_records':1,'snapshot_cutoff':None,'missing_snapshot_slots':[],
              'historical_missing_slots':[],'operational_missing_slots':[],'paused_missing_slots':[],
              'coverage_registered':False,'components':{'hourly':'unknown','intervals':'unknown','snapshot':'unknown'},
              'notes':['数据读取失败，不能确认覆盖完整'],'scope':'乘方经营报表与商品视频全域状态列表分开核对。'}
    quality=read('data_read','数据完整性记录',lambda:data_quality.current(root,env,now,windows=windows,
                                                                 stale_minutes=cfg['snapshot_stale_minutes']),fallback)
    data_ok='data_read' not in issues
    coverage_ok='coverage_read' not in issues
    data_codes=('hours','invalid','intervals')
    monitor_codes=('snapshot:plan','snapshot:material','balance','read:plan','read:material',
                   'time:plan','time:material','balance_read','balance_time')
    if grace:
        for code in (*data_codes,*monitor_codes):deferred[code]='启动宽限期，等待实际复核'
    else:
        if data_ok:
            verified.update(('hours','invalid'))
            if quality['missing_hours']:issues['hours']='今日完整小时缺失：'+'、'.join(quality['missing_hours'])+'；不以0补造。'
            if quality['invalid_records']:issues['invalid']='本地记录校验失败，请重新采集核对。'
        else:
            for code in data_codes:deferred[code]='数据读取失败，等待复核'
        if monitoring:
            ms=monitor.Store(root)
            for kind,label in (('plan','计划'),('material','素材')):
                if kind not in active_kinds:continue
                series=read('read:'+kind,label+'快照',lambda:ms.snapshots(account,kind,day) if ms.path.exists() else [],None)
                if series is not None:
                    stale=read('time:'+kind,label+'快照时间',lambda:not series or
                               age(series[-1].get('request_end') or series[-1]['fetched_at'])>cfg['snapshot_stale_minutes'],None)
                    if stale is not None:
                        verified.add('snapshot:'+kind)
                        if stale:issues['snapshot:'+kind]=label+'累计快照缺失或过期，相关异常判断不可用。'
                    else:deferred['snapshot:'+kind]='快照时间校验失败，等待复核'
                else:
                    deferred['snapshot:'+kind]='快照读取失败，等待复核'
                    deferred['time:'+kind]='快照读取失败，等待复核'
            balances=read('balance_read','余额记录',lambda:ms.balances(account,now-timedelta(hours=2)) if ms.path.exists() else [],None)
            if balances is not None:
                stale=read('balance_time','余额时间',lambda:not balances or age(balances[-1]['fetched_at'])>cfg['snapshot_stale_minutes'],None)
                if stale is not None:
                    verified.add('balance')
                    if stale:issues['balance']='余额样本缺失或过期，速率及续航预测可能不可用。'
                else:deferred['balance']='余额时间校验失败，等待复核'
            else:
                deferred['balance']='余额读取失败，等待复核';deferred['balance_time']=deferred['balance']
            if data_ok and coverage_ok:
                verified.add('intervals')
                if quality['operational_missing_slots']:
                    issues['intervals']='启用后漏采%d个半小时观察点，区间分析受限；请检查采集服务。'%len(quality['operational_missing_slots'])
            elif coverage_ok is False:deferred['intervals']='采集起点无法读取，等待复核'
    if not monitoring:
        for code in ('intervals',*monitor_codes):
            verified.discard(code);deferred[code]='采集监测已暂停，等待重新启用后复核'
    opts=daily_brief.options(root);due=now.replace(hour=opts['hour'],minute=opts['minute'],second=0,microsecond=0)
    target=(now.date()-timedelta(days=1)).isoformat()
    if opts['enabled'] and now>=due+timedelta(minutes=cfg['daily_late_minutes']):
        path=root/'data/qianchuan/daily_brief.db'
        def daily_job():
            if not path.exists():return None
            with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True,timeout=10)) as db:
                return db.execute('SELECT sent FROM jobs WHERE account=? AND day=?',(account,target)).fetchone()
        row=read('daily_read','日报任务库',daily_job,None)
        if 'daily_read' not in issues:
            verified.add('daily')
            if not row or not row[0]:issues['daily']='昨日重点日报尚未确认发送，请检查任务与目标群。'
        else:deferred['daily']='日报任务读取失败，等待复核'
    else:
        deferred['daily']='日报未到检查时间或已暂停，等待复核';deferred['daily_read']=deferred['daily']
    advisories=[]
    if not grace and monitoring and data_ok and coverage_ok and quality['historical_missing_slots']:
        advisories.append('启用前缺少%d个历史半小时观察点。仅提示一次；这部分区间分析受限，不直接证明最新累计金额缺失。'%len(quality['historical_missing_slots']))
    if grace:assessment='启动宽限期：部分检查等待复核，未将跳过检查视为恢复。'
    elif not monitoring:assessment='采集监测已暂停：相关告警保留状态，等待启用后复核。'
    else:assessment='已分别检查小时覆盖、半小时观察点和最新快照时效。'
    if not cfg['enabled']:assessment='健康通知已暂停；'+assessment
    return {'account':account,'checked_at':now.isoformat(),'issues':issues,'verified':sorted(verified),
            'deferred':deferred,'advisories':advisories,'assessment':assessment,'quality':quality,'signals':signals,'subresults':subresults(root,account,signals),
            'scopes':{'hours':day,'intervals':day,'daily':target},
            'limit':'心跳仅证明检查线程运行，不证明飞书长连接已连通；历史观察点缺口不等于累计金额缺失。'}




def destination(env):
    chat = (env.get('FEISHU_HEALTH_CHAT_ID') or env.get('FEISHU_ALERT_CHAT_ID') or env.get('FEISHU_CHAT_ID') or '').strip()
    if not chat or chat not in set(feishu_allowed_chat_ids(env) or [env.get('FEISHU_CHAT_ID')]):
        raise ValueError('健康预警群未配置或不在白名单')
    return chat


def issue_label(code):
    return {'intervals':'运行期间半小时漏采','hours':'小时报表覆盖','invalid':'数据记录校验',
            'listener':'监听进程心跳','balance':'余额样本时效','daily':'昨日重点日报',
            'snapshot:plan':'计划快照时效','snapshot:material':'素材快照时效',
            'data_read':'数据完整性读取','coverage_read':'采集启用记录读取','daily_read':'日报任务读取',
            'balance_read':'余额记录读取','balance_time':'余额时间校验',
            'read:plan':'计划快照读取','read:material':'素材快照读取',
            'time:plan':'计划快照时间校验','time:material':'素材快照时间校验'}.get(code,code.removeprefix('task:'))


def rendered(status):
    messages=list(status['issues'].values())
    if not messages:messages=['未发现本轮已完成检查中的运行故障；历史覆盖限制请见下方。']
    return card('投流助手｜运行与数据检查',[
        text_element('账户%s｜%s'%(status['account'],status['checked_at'])),
        text_element(status['assessment']),
        text_element(('等待复核：'+'；'.join(dict.fromkeys(status['deferred'].values()))+'；' if status['deferred'] else '本轮检查已完成；')+
                     ('发现%d项问题。'%len(status['issues']) if status['issues'] else '本轮已执行检查未发现问题。')),
        text_element('\n'.join(messages)),
        text_element(data_quality.summary(status['quality'])),
        text_element('半小时缺口为当日累计记录，不代表本轮新增；当前区间能否分析可查询“采集与速率”。'),
        text_element(status['quality']['scope']),text_element(status['limit'])])


def check(root,env,notifier_factory=None,now=None,send=False):
    now=now or h.now_china();cfg=options(root);status=inspect(root,env,now)
    if not send or not cfg['enabled']:return status
    account=status['account'];chat=destination(env);store=Store(root)
    from .monitor import add_mentions
    config=settings.load_config(Path(root)/'config/rules.json')
    config=dict(config,monitoring={'at_roles':{'health':cfg['at_roles']}})
    def notice_id(code,since,exists):
        return hashlib.sha256((account+':'+code+':'+since+':'+str(exists)).encode()).hexdigest()[:40]
    def enqueue(db,key,code,kind,message):
        payload=card('投流助手｜'+{'alert':'运行预警','recovery':'恢复通知','info':'数据覆盖说明'}[kind],
                     [text_element('账户'+account),text_element(message),text_element(status['checked_at'])])
        if kind!='info':payload=add_mentions(payload,config,'health')
        db.execute('INSERT OR IGNORE INTO notices(id,account,chat,payload) VALUES(?,?,?,?)',
                   (key,account,chat,json.dumps(payload,ensure_ascii=False)))
        scope=status['quality']['date'] if kind=='info' else status['scopes'].get(code)
        db.execute('INSERT OR IGNORE INTO notice_context VALUES(?,?,?,?)',(key,code,kind,scope))
    with file_lock(store.path.with_suffix('.lock')):
        with store.db() as db:
            previous=db.execute('SELECT stamp FROM assessment_clock WHERE account=?',(account,)).fetchone()
            if previous and data_quality.stamp(previous[0])>now:return status
            db.execute('INSERT OR REPLACE INTO assessment_clock VALUES(?,?)',(account,now.isoformat()))
            # Old queued messages lack evidence state; do not replay misleading v23/v24 recoveries.
            db.execute('UPDATE notices SET sent=2 WHERE account=? AND sent=0 AND id NOT IN (SELECT id FROM notice_context)',(account,))
            old={code:(since,active) for code,since,active in db.execute('SELECT code,since,active FROM incidents WHERE account=?',(account,))}
            for code in set(old)|set(status['issues']):
                # Missing from issues alone is never recovery. Explicit assessment is required.
                if code not in status['verified']:continue
                exists=code in status['issues'];since,active=old.get(code,(now.isoformat(),0))
                scope=status['scopes'].get(code)
                row=db.execute('SELECT scope FROM incident_scopes WHERE account=? AND code=?',(account,code)).fetchone()
                previous_scope=row[0] if row else None
                if scope and code in old and previous_scope is None:
                    day=data_quality.stamp(since).date()
                    previous_scope=(day-timedelta(days=1)).isoformat() if code=='daily' else day.isoformat()
                changed_scope=scope is not None and previous_scope is not None and scope!=previous_scope
                historical_only=(code=='intervals' and not exists and row is None and
                                 bool(status['quality']['historical_missing_slots'] or status['quality']['paused_missing_slots']))
                if changed_scope or historical_only:
                    # New date/policy classification cannot prove a historical gap was filled.
                    for key in (notice_id(code,since,True),notice_id(code,since,False)):
                        db.execute('UPDATE notices SET sent=2 WHERE id=? AND sent=0',(key,))
                    db.execute('DELETE FROM incidents WHERE account=? AND code=?',(account,code))
                    db.execute('DELETE FROM incident_scopes WHERE account=? AND code=?',(account,code))
                    if not exists:continue
                    since,active=now.isoformat(),0
                if exists:
                    queued=db.execute('SELECT sent FROM notices WHERE id=?',(notice_id(code,since,True),)).fetchone()
                    if active and (not queued or queued[0]==2):
                        # A retired legacy/obsolete queued alert is not a delivered alert.
                        # Reconfirm the still-real fault instead of leaving it active but silent forever.
                        replacement=now.isoformat()
                        since=replacement if replacement!=since else (now+timedelta(microseconds=1)).isoformat()
                        active=0
                    if not active and (now-data_quality.stamp(since)).total_seconds()>=cfg['incident_delay_minutes']*60:
                        active=1;enqueue(db,notice_id(code,since,True),code,'alert',status['issues'][code])
                    db.execute('INSERT OR REPLACE INTO incidents VALUES(?,?,?,?)',(account,code,since,active))
                    if scope:db.execute('INSERT OR REPLACE INTO incident_scopes VALUES(?,?,?)',(account,code,scope))
                else:
                    # Only notify recovery if an alert was actually sent, rather than merely queued.
                    sent=db.execute('SELECT sent FROM notices WHERE id=?',(notice_id(code,since,True),)).fetchone()
                    if active and sent and sent[0]==1:
                        enqueue(db,notice_id(code,since,False),code,'recovery',
                                issue_label(code)+'：已实际复核，本项告警条件已解除；其他检查项请查看“健康检查”。')
                    db.execute('UPDATE notices SET sent=2 WHERE id=? AND sent=0',(notice_id(code,since,True),))
                    db.execute('DELETE FROM incidents WHERE account=? AND code=?',(account,code))
                    db.execute('DELETE FROM incident_scopes WHERE account=? AND code=?',(account,code))
            if status['advisories']:
                day=status['quality']['date']
                inserted=db.execute('INSERT OR IGNORE INTO advisories VALUES(?,?,?)',(account,day,'historical'))
                if inserted.rowcount:
                    key=hashlib.sha256(('historical:'+account+':'+day).encode()).hexdigest()[:40]
                    enqueue(db,key,'historical','info',status['advisories'][0])
        with store.db() as db:
            pending=db.execute('SELECT n.id,n.chat,n.payload,c.code,c.kind,c.scope FROM notices n JOIN notice_context c ON n.id=c.id WHERE n.account=? AND n.sent=0 ORDER BY n.rowid',(account,)).fetchall()
            for key,bound,payload,code,kind,scope in pending:
                if bound!=chat:raise ValueError('待发送健康通知已绑定其他群，请人工核对')
                current_scope=status['quality']['date'] if kind=='info' else status['scopes'].get(code)
                if scope and scope!=current_scope:
                    db.execute('UPDATE notices SET sent=2 WHERE id=?',(key,));continue
                if kind=='info':
                    if not status['advisories']:continue
                elif code not in status['verified']:continue
                elif kind=='alert' and code not in status['issues']:
                    db.execute('UPDATE notices SET sent=2 WHERE id=?',(key,));continue
                elif kind=='recovery' and code in status['issues']:
                    db.execute('UPDATE notices SET sent=2 WHERE id=?',(key,));continue
                notifier_factory(chat).send_card(json.loads(payload),request_uuid=key)
                db.execute('UPDATE notices SET sent=1 WHERE id=?',(key,));db.commit()
    return status


class Engine:
    def __init__(self, root, env, factory, workers, worker_tick=None):
        self.root, self.env, self.factory, self.workers = root, env, factory, workers
        self.worker_tick = worker_tick
        self.stopping = threading.Event(); self.thread = None
        self.gaps = diagnostics.GapTracker()

    def work(self):
        while not self.stopping.is_set():
            try:
                self.gaps.observe()
                store = Store(self.root); account = h.account_id(self.env)
                store.put(account,'listener','ok')
                for name, getter in self.workers.items():
                    thread = getter()
                    store.put(account,'线程:'+name,'ok' if thread and thread.is_alive() else 'failed',
                              '' if thread and thread.is_alive() else 'ThreadStopped')
                if self.worker_tick:
                    blocked = time.monotonic()-self.worker_tick() > options(self.root)['task_timeout_minutes']*60
                    store.put(account,'消息处理进度','failed' if blocked else 'ok','WorkerStalled' if blocked else '')
                check(self.root,self.env,self.factory,send=True)
            except Exception as exc:
                print('运行健康巡检待重试（%s），请查看独立巡检结果。' % type(exc).__name__,flush=True)
            self.stopping.wait(60)

    def start(self):
        Store(self.root).put(h.account_id(self.env),'started','ok')
        self.thread = threading.Thread(target=self.work,name='qianchuan-health',daemon=True); self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread: self.thread.join(timeout=2)

# health-assessment-v25
VERSION = 25
