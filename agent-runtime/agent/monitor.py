"""Persistent half-hour snapshots, anomaly notices and account cash runway checks.

Snapshot differences are observations, not an exact event-time half-hour report.
All external operations are reads except explicitly configured Feishu notices.
"""
import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from . import hourly as h, qianchuan as q, qc_auth, account_balance, settings
from .card_view import card as base_card, text_element
from .knowledge import Knowledge, tokens
from .notify import feishu_allowed_chat_ids
from .reliability import file_lock
from . import analysis_gate as gate

D = Decimal
METRICS = dict(h.FIELDS, order_count='total_pay_order_count_for_roi2')
TOPICS = {'plan': (q.TOPIC, 'ad_id', 'ad_name'),
          'material': ('SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO', 'material_id', 'roi2_material_video_name')}


def card(title, elements, template='blue'):
    value = base_card(title, elements)
    value['header']['template'] = template
    return value


def add_mentions(rendered, config, category, now=None):
    """Only administrator-configured identities become native Feishu mentions."""
    if category == "balance_check":
        local = (now or h.now_china()).astimezone(h.CHINA)
        if local.hour not in (10, 12, 14, 16, 18):
            return rendered
    roles = config.get('notify', {}).get('roles', {})
    selected = config.get('monitoring', {}).get('at_roles', {}).get(category, [])
    identities = []
    for role in selected:
        for identity in roles.get(role, []):
            if not isinstance(identity, str): continue
            identity = identity.strip()
            if identity in identities: continue
            # Do not turn an accidental value into @all or allow tag injection.
            if identity != 'all' and re.fullmatch(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', identity):
                identities.append(identity)
            elif identity != 'all' and re.fullmatch(r'[A-Za-z0-9_\-]+', identity):
                identities.append(identity)
    if identities:
        mentions = ['<at email="%s"></at>' % ident if '@' in ident else '<at id="%s"></at>' % ident for ident in identities]
        rendered['elements'].append({'tag': 'div', 'text': {'tag': 'lark_md', 'content': '请关注：'+' '.join(mentions)}})
    return rendered


def bucket(now):
    return now.astimezone(h.CHINA).replace(minute=30*(now.minute//30), second=0, microsecond=0)


def fetch_snapshot(env, kind, now=None, request=None):
    now = now or h.now_china()
    topic, identity, name = TOPICS[kind]
    params = {'advertiser_id': int(h.account_id(env)), 'data_topic': topic,
              'dimensions': [identity, name, 'stat_time_day'], 'metrics': list(METRICS.values()),
              'filters': [], 'order_by': [], 'start_time': now.strftime('%Y-%m-%d 00:00:00'),
              'end_time': now.strftime('%Y-%m-%d %H:%M:%S'), 'page_size': 100}
    rows, raw, seen, expected = [], [], set(), None
    for page in range(1, 1001):
        params['page'] = page
        from . import diagnostics
        with diagnostics.api_label('qianchuan.snapshot'):
            response = qc_auth.call(env, lambda token: (request or q._get)(dict(params), token))
        try:
            data = response['data']; info = data['page_info']; batch = data['rows']
            count, pages = int(info['total_number']), int(info['total_page'])
            if not isinstance(batch, list) or int(info['page']) != page or count < 0: raise ValueError()
            if count == 0:
                if page != 1 or batch or pages not in (0, 1): raise ValueError()
                raw.append(response); break
            if expected is None: expected = (count, pages)
            if expected != (count, pages) or not batch or not 1 <= page <= pages: raise ValueError()
            for value in batch:
                dims = value['dimensions']; pid = str(dims[identity]['Value'])
                label = str(dims[name]['Value'])
                if not pid.isascii() or not pid.isdigit() or not label.strip() or pid in seen: raise ValueError()
                if dims['stat_time_day']['ValueStr'] != now.date().isoformat(): raise ValueError()
                seen.add(pid)
                row = {'id': pid, 'name': label}
                for key, field in METRICS.items(): row[key] = str(q.number(value['metrics'][field]['Value'], field))
                if D(row['order_count']) != D(row['order_count']).to_integral_value(): raise ValueError()
                rows.append(row)
            raw.append(response)
            if page == pages:
                if len(rows) != count: raise ValueError()
                break
        except (KeyError, ValueError, TypeError):
            raise ValueError('累计快照字段、分页或金额校验失败') from None
    else:
        raise ValueError('累计快照超出分页上限')
    return {'account': h.account_id(env), 'kind': kind, 'day': now.date().isoformat(),
            'bucket': bucket(now).isoformat(), 'fetched_at': h.now_china().isoformat(),
            'request_end': now.isoformat(), 'topic': topic, 'rows': rows, 'raw_pages': raw,
            'scope': '乘方商品计划累计报表' if kind == 'plan' else '商品视频素材累计报表',
            'unit': 'CNY', 'warning': '相邻快照差分含退款、归因及数据回补影响，非精确半小时事件归属。'}


class Store:
    def __init__(self, root): self.path = Path(root)/'data/qianchuan/monitor.db'

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots(account TEXT,kind TEXT,bucket TEXT,stamp TEXT,payload TEXT,PRIMARY KEY(account,kind,bucket))')
            db.execute('CREATE TABLE IF NOT EXISTS balances(account TEXT,stamp TEXT,payload TEXT,PRIMARY KEY(account,stamp))')
            db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,account TEXT,stamp TEXT,payload TEXT,sent INTEGER DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS cooldown(account TEXT,entity TEXT,stamp TEXT,PRIMARY KEY(account,entity))')
            with db: yield db
        finally: db.close()

    def save_snapshot(self, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?)', (value['account'],value['kind'],value['bucket'],value['fetched_at'],json.dumps(value,ensure_ascii=False)))

    def snapshots(self, account, kind, day):
        with self.db() as db:
            return [json.loads(x[0]) for x in db.execute('SELECT payload FROM snapshots WHERE account=? AND kind=? AND bucket>=? AND bucket<? ORDER BY bucket',
                (account,kind,day+'T00:00:00+08:00',(datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()+'T00:00:00+08:00'))]

    def save_balance(self, value):
        with self.db() as db: db.execute('INSERT OR REPLACE INTO balances VALUES(?,?,?)',(value['advertiser_id'],value['fetched_at'],json.dumps(value,ensure_ascii=False)))

    def balances(self, account, since):
        with self.db() as db:
            return [json.loads(x[0]) for x in db.execute('SELECT payload FROM balances WHERE account=? AND stamp>=? ORDER BY stamp',(account,since.isoformat()))]

    def exists(self, key):
        with self.db() as db: return db.execute('SELECT 1 FROM events WHERE id=?',(key,)).fetchone() is not None

    def enqueue(self, key, account, now, rendered, entity=None):
        with self.db() as db:
            inserted=db.execute('INSERT OR IGNORE INTO events(id,account,stamp,payload) VALUES(?,?,?,?)',(key,account,now.isoformat(),json.dumps(rendered,ensure_ascii=False)))
            if inserted.rowcount and entity:
                db.execute('INSERT OR REPLACE INTO cooldown VALUES(?,?,?)',(account,entity,now.isoformat()))

    def pending(self, account):
        with self.db() as db: return db.execute('SELECT id,payload,stamp FROM events WHERE account=? AND sent=0 ORDER BY stamp',(account,)).fetchall()

    def mark_sent(self, key, state=1):
        with self.db() as db: db.execute('UPDATE events SET sent=? WHERE id=?',(state,key))

    def reserve_alert(self, account, entity, now, cooldown):
        with self.db() as db:
            row=db.execute('SELECT stamp FROM cooldown WHERE account=? AND entity=?',(account,entity)).fetchone()
            if row and (now-datetime.fromisoformat(row[0])).total_seconds() < cooldown: return False
            db.execute('INSERT OR REPLACE INTO cooldown VALUES(?,?,?)',(account,entity,now.isoformat()))
        return True


def changes(older,newer):
    return gate.interval(older,newer)


def anomalies(series,cfg,model_cfg,now=None,account=None,kind=None,assessment=None):
    if assessment is None:
        if not series:return []
        assessment=gate.assess(series,account or series[-1].get('account'),kind or series[-1].get('kind'),now or h.now_china())
    if assessment['state'] not in ('ready','partial'):return []
    findings=[]
    for pid,row in assessment['entities'].items():
        old,current=row['previous'],row['current'];reasons=[]
        core=gate.usable(row,'ad_spend','gmv');sales=gate.usable(row,'gmv','order_count')
        spend=current.get('ad_spend');gmv=current.get('gmv');orders=current.get('order_count')
        roi=gmv/spend if core and spend else None
        profile=model_cfg.get('plans',{}).get(pid,{}) if assessment['kind']=='plan' else {}
        refund=model_cfg.get('refund_rates',{}).get(model_cfg.get('season','autumn_winter'),{}).get(profile.get('refund_band'))
        margin=1-1/D(str(model_cfg['purchase_multiplier']))-D(str(model_cfg['insurance_rate']))-D(str(model_cfg['platform_rate']))
        breakeven=1/((1-D(str(refund)))*margin) if refund is not None and D(str(refund))<1 else None
        if core:
            before_spend=old['ad_spend']/row['hours'][0];spend_speed=spend/row['hours'][1]
            if spend>=D(str(cfg['spend_min'])) and gmv==0:reasons.append('观察区间有明显消耗但无成交')
            elif (spend>=D(str(cfg['spend_min'])) and old['ad_spend']>=D(str(cfg['previous_spend_min']))
                  and spend_speed>=before_spend*D(str(cfg['spike_multiple'])) and breakeven and roi<breakeven):
                reasons.append('消耗加速且整体支付ROI低于对应历史退款档的保本线')
            elif (spend>=D(str(cfg['spend_min'])) and old['ad_spend']>=D(str(cfg['previous_spend_min']))
                  and spend_speed>=before_spend*D(str(cfg['spike_multiple'])) and old['gmv']>0
                  and roi<=(old['gmv']/old['ad_spend'])/D(2)):
                reasons.append('消耗加速且整体支付ROI较前观察区间下降至少50%，需核对转化')
        if sales:
            before_gmv=old['gmv']/row['hours'][0];gmv_speed=gmv/row['hours'][1]
            if (gmv>=D(str(cfg['sales_min'])) and orders>=D(str(cfg['orders_min']))
                    and (before_gmv==0 or gmv_speed>=before_gmv*D(str(cfg['spike_multiple'])))):
                reasons.append('成交金额及订单数快速增加，需关注起量')
        if reasons:
            value=lambda amount:None if amount is None else str(amount)
            findings.append({'kind':assessment['kind'],'id':pid,'name':row['name'],'reasons':reasons,
                'start':assessment['start'],'end':assessment['end'],
                'spend':value(spend),'gmv':value(gmv),'orders':value(orders),'payment_roi':value(roi),
                'breakeven_roi':value(breakeven),'previous_spend':value(old.get('ad_spend')),
                'previous_gmv':value(old.get('gmv')),'metric_admission':row['metrics']})
    return findings

def balance_rate(samples, current, hours=2, tolerance_minutes=5):
    from .balance_cycle import rate
    return rate(samples, current, hours, tolerance_minutes)


def balance_notice(snapshot, rate):
    from .balance_cycle import notice
    return notice(snapshot, rate)


class Engine:
    def __init__(self,root,env,notifier_factory):
        self.root=Path(root);self.env=env;self.store=Store(root);self.notifier_factory=notifier_factory
        self.stopping=threading.Event();self.thread=None;self.trial_thread=None;self.last_problem=None

    def sample_balance(self):
        value=account_balance.fetch(self.env);self.store.save_balance(value)
        from . import balance_watch
        balance_watch.observe(self.store,value,settings.load_config(self.root/'config/rules.json'))
        return value

    def tick(self,now=None):
        now=now or h.now_china()
        if now.tzinfo is None: raise ValueError('余额调度时间必须带时区')
        now=now.astimezone(h.CHINA);account=h.account_id(self.env)
        config=settings.load_config(self.root/'config/rules.json');cfg=config.get('monitoring',{})
        if not cfg.get('enabled'): return
        period=bucket(now);stamp=period.isoformat();errors=[];admissions={}
        with file_lock(self.store.path.with_suffix('.lock')):
            gate.prepare_store(self.store,account)
            for kind in settings.collection_kinds(config,account):
                collected=False
                try:
                    series=self.store.snapshots(account,kind,now.date().isoformat())
                    if not series or series[-1]['bucket']!=stamp:
                        self.store.save_snapshot(fetch_snapshot(self.env,kind,now))
                    series=self.store.snapshots(account,kind,now.date().isoformat())
                    collected=True
                    admission=gate.assess(series,account,kind,now,stale_minutes=gate.stale_minutes(self.root))
                    admissions[kind]=admission
                    gate.record(self.store,admission,now,notify_observation=cfg.get('observation_notices_enabled',True))
                    for finding in anomalies(series,cfg,config['model'],assessment=admission):
                        key='anomaly26:'+account+':'+kind+':'+finding['id']+':'+stamp
                        if self.store.exists(key):continue
                        # New alert categories can still surface during another category's cooldown.
                        entity = kind+':'+finding['id']+':'+hashlib.sha256('|'.join(finding['reasons']).encode()).hexdigest()[:12]
                        with self.store.db() as db:
                            row=db.execute('SELECT stamp FROM cooldown WHERE account=? AND entity=?',(account,entity)).fetchone()
                        if row and (now-datetime.fromisoformat(row[0])).total_seconds()<cfg['cooldown_minutes']*60:continue
                        fallback=gate.finding_text(finding)
                        rendered=card('投流监测｜规则异常',[
                            text_element(finding['start']+'—'+finding['end']),text_element(fallback),
                            text_element('快照观测变化；只提醒，不自动调预算。')],template='orange')
                        rendered['_brief_alert']={'kind':kind,'window':finding['start']+'—'+finding['end'],'text':fallback}
                        self.store.enqueue(key,account,now,add_mentions(rendered,config,'anomaly'),entity=entity)
                        gate.bind(self.store,key,account,finding)
                except Exception as exc:
                    errors.append(type(exc).__name__)
                    if not collected:
                        admissions[kind]=gate.failed(account,kind,now)
                        gate.record(self.store,admissions[kind],now,notify_observation=cfg.get('observation_notices_enabled',True))
            # Continuous half-hour samples retain cross-midnight two-hour baselines.
            samples=self.store.balances(account,now-timedelta(hours=5))
            latest=samples[-1] if samples else None
            if not latest or bucket(datetime.fromisoformat(latest['fetched_at']))!=period:
                try:latest=self.sample_balance();samples=self.store.balances(account,now-timedelta(hours=5))
                except Exception as exc:latest=None;errors.append(type(exc).__name__)
            covered_notice=None
            if now.hour in range(0,24,2) and now.minute<10:
                key='balance2h:'+account+':'+now.date().isoformat()+':'+str(now.hour)
                if not self.store.exists(key) and latest:
                    rate=balance_rate(samples,latest)
                    rendered=balance_notice(latest,rate)
                    category='balance_alert' if rendered['header']['template']=='red' else 'balance_check'
                    from . import balance_comparison
                    rendered=balance_comparison.attach(self.root,self.store,account,now,latest,rendered)
                    self.store.enqueue(key,account,now,add_mentions(rendered,config,category,now=now))
                    if rendered['header']['template']=='red':covered_notice=key
                elif not self.store.exists(key) and latest is None:
                    rendered=card('千川余额｜检查失败',[
                        text_element('%s（北京时间）｜当前余额读取失败，暂无法估算可用时长。请核对资金，系统将重试。'%now.strftime('%H:%M'))],template='red')
                    from . import balance_comparison
                    rendered=balance_comparison.attach(self.root,self.store,account,now,None,rendered)
                    self.store.enqueue(key+':unavailable',account,now,add_mentions(rendered,config,'balance_alert'))
            if latest:
                from . import balance_watch
                try:balance_watch.observe(self.store,latest,config,now=now,covered_notice=covered_notice)
                except Exception as exc:errors.append(type(exc).__name__)
            # One configured alert group, not every group the listener can answer in.
            allowed=set(feishu_allowed_chat_ids(self.env) or [self.env.get('FEISHU_CHAT_ID')])
            chat=self.env.get('FEISHU_ALERT_CHAT_ID') or self.env.get('FEISHU_CHAT_ID')
            if not chat or chat not in allowed: raise ValueError('预警目标群不在白名单内')
            notifier=self.notifier_factory(chat)
            from . import alert_digest
            kinds=settings.collection_kinds(config,account)
            merged=alert_digest.deliver(self.store,account,chat,now,notifier,lambda key:any(key.startswith('anomaly26:'+account+':'+k+':') for k in kinds) and gate.allow_pending(self.store,key,account,admissions))
            for key,payload,created in self.store.pending(account):
                if key in merged:continue
                if any(key.startswith("anomaly26:"+account+":"+kind+":") for kind in ("plan","material") if kind not in settings.collection_kinds(config,account)):continue
                if key.startswith('balance4h:'):continue  # Preserve old outbox without replaying old cadence.
                if key.startswith('balance:') and not key.startswith('balance:threshold-v2:'):
                    self.store.mark_sent(key,state=2)  # Retire queued notices from the previous schedule.
                    continue
                if (now-datetime.fromisoformat(created)).total_seconds()>30*60:
                    self.store.mark_sent(key,state=2)  # Do not replay stale financial warnings after a long outage.
                    continue
                if key.startswith('anomaly26:') and not gate.allow_pending(self.store,key,account,admissions):continue
                uuid=hashlib.sha256((chat+':'+key).encode()).hexdigest()[:40]
                rendered=json.loads(payload)
                if '_balance_comparison' in rendered:
                    from . import balance_comparison
                    balance_comparison.deliver(self.root,notifier,key,rendered,uuid)
                else:
                    notifier.send_card(rendered,request_uuid=uuid)
                self.store.mark_sent(key)
            if errors:raise RuntimeError('部分监测采集未完成，将重试')

    def work(self):
        from . import health
        while not self.stopping.is_set():
            try:health.run(self.root, self.env, '半小时监测', self.tick);self.last_problem=None
            except Exception as exc:
                problem=type(exc).__name__
                if problem!=self.last_problem:print('半小时监测/余额检查暂未完成，将重试（%s）。'%problem,flush=True)
                self.last_problem=problem
            self.stopping.wait(60)

    def start(self):
        if (self.root/'config/balance_background_20261102.enabled').exists():
            from . import balance_background
            self.trial_thread=threading.Thread(target=balance_background.work,args=(self,),name='balance-background-5m',daemon=True)
            self.trial_thread.start()
        self.thread=threading.Thread(target=self.work,name='qianchuan-monitor',daemon=True);self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:self.thread.join(timeout=2)
        if self.trial_thread:self.trial_thread.join(timeout=2)


def today_context(root,env,question,now=None):
    now=now or h.now_china();store=Store(root)
    if not store.path.exists():return None
    account=h.account_id(env);series=store.snapshots(account,'plan',now.date().isoformat())
    if not series:return None
    admission=gate.assess(series,account,'plan',now,stale_minutes=gate.stale_minutes(root))
    if not admission['cumulative_valid']:raise ValueError('最新累计快照校验失败，未生成数据分析')
    latest=series[-1];rows=latest['rows'];query=tokens(question)
    ranked=sorted(rows,key=lambda p:(-int(p['id'] in question),-len(query&tokens(p['name'])),-D(p['ad_spend'])))
    totals={k:sum((D(p[k]) for p in rows),D(0)) for k in METRICS}
    totals['payment_roi']=totals['gmv']/totals['ad_spend'] if totals['ad_spend'] else None
    previous=changes(series[-2],latest) if len(series)>1 else None
    if admission['state'] not in ('ready','partial'):previous=None
    plans=h.Store(root).plans(account)
    if plans and (plans.get('scope')!=h.PLAN_SCOPE or datetime.fromisoformat(plans['fetched_at']).date()!=now.date()):plans=None
    from . import data_quality
    quality = data_quality.current(root, env, now, stale_minutes=gate.stale_minutes(root))
    return json.dumps({'异常分析准入': gate.public(admission), '分析限制': gate.describe(admission), '数据完整性检查': quality, '资料类型':'今日半小时采集','账户':account,'日期':now.date().isoformat(),
        '数据截止时间':admission.get('cutoff'), '累计范围':'今日00:00至'+admission['cutoff'],'最新采集时间':latest['fetched_at'],
        '快照次数':len(series),'ROI口径':'整体成交金额/整体消耗','整体':totals,'有报表记录计划数':len(rows),
        '逐计划分析资格':[{'id':p['id'],'metrics':admission['entities'].get(p['id'],{}).get('metrics',{}),
                         '说明':'仅ready指标可用于趋势判断；空资格表表示全局条件不足'} for p in ranked[:20]],
        '逐计划':ranked[:20],'最近区间观测增量':list(previous.values())[:20] if previous else None,
        '状态列表读取时间':plans['fetched_at'] if plans else None,
        '状态列表计划总数':len(plans['plans']) if plans else None,
        '状态列表在投数':sum(p['status']=='DELIVERY_OK' and p['opt_status']=='ENABLE' for p in plans['plans']) if plans else None,
        '统计范围':{'经营':latest['scope'],'状态':h.PLAN_SCOPE},
        '限制':'今日累计快照每半小时更新，可能含进行中小时；仅截至标明的采集时间。快照差分是观测增量，不是精确30分钟事件归属，可能有退款和归因回补。经营范围仅乘方计划，状态为商品视频全域，不含直播。最多提供20条相关计划，可指定ID或名称查询其余。没有利润预测或预算金额。'},ensure_ascii=False,default=str)

# health-v23

# balance-v24

# analysis-admission-v26
