"""Projection of deployed evidence. Never imports production code or reads credentials."""
from contextlib import contextmanager
from datetime import datetime,timedelta
import hashlib,json,sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

class Source:
    def __init__(self,root,history_path=None):self.root=Path(root).resolve();self.history_path=history_path
    def inside(self,relative):
        p=(self.root/relative).resolve()
        if not p.is_relative_to(self.root):raise ValueError('source_path_escape')
        return p
    @contextmanager
    def database(self,name):
        if name not in ('monitor.db','hourly.db'):raise ValueError('database_not_allowed')
        path=self.inside('data/qianchuan/'+name)
        if not path.is_file():raise FileNotFoundError(name)
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)
        try:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN');yield db
        finally:db.close()
    def accounts(self):
        with self.database('monitor.db') as db:
            return [r[0] for r in db.execute('SELECT DISTINCT account FROM snapshots UNION SELECT DISTINCT account FROM balances ORDER BY account')]
    def fingerprint(self):
        result={}
        for name in ('agent/monitor.py','agent/hourly.py','agent/balance_cycle.py','agent/analysis_gate.py','config/rules.json'):
            p=self.inside(name)
            result[name]=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
        return result
    def snapshot(self,account,as_of):
        now=datetime.fromisoformat(as_of)
        if now.tzinfo is None:raise ValueError('timezone_required')
        now=now.astimezone(ZoneInfo('Asia/Shanghai'));cutoff=now.isoformat();since=(now-timedelta(days=31)).date().isoformat()
        gaps=['first_enabled_source_missing','complete_daily_orders_unverified','budget_baseline_missing','rule_clock_not_started',
              'approval_and_action_history_unverified','cross_store_atomicity_unverified','freshness_policy_unverified']
        with self.database('monitor.db') as db:
            raw=db.execute("SELECT bucket,payload FROM snapshots WHERE account=? AND kind='plan' AND bucket>=? AND stamp<=? ORDER BY bucket",(account,since,cutoff)).fetchall()
            balance=db.execute('SELECT stamp,payload FROM balances WHERE account=? AND stamp<=? ORDER BY stamp DESC LIMIT 1',(account,cutoff)).fetchone()
        plans={};refs=[]
        def plan(pid):
            return plans.setdefault(pid,{'plan_id':pid,'scope':'unverified','first_enabled_on':None,'daily':[],
                'observations':[],'hourly_observations':[],'status':None,'budget':None,'refund':None,
                'missing':['first_enabled_on','complete_daily_facts','fixed_budget_baseline','budget_change_history','state_events']})
        for bucket,payload in raw:
            s=json.loads(payload);ref='monitor.db:snapshots:'+bucket
            if s.get('account')!=account or s.get('kind')!='plan' or s.get('unit')!='CNY':gaps.append('snapshot_identity_or_unit_invalid');continue
            if s.get('scope')!='乘方商品计划累计报表':gaps.append('snapshot_scope_unverified');continue
            refs.append({'ref':ref,'sha256':hashlib.sha256(payload.encode()).hexdigest()})
            for row in s.get('rows',[]):
                p=plan(str(row['id']));p['scope']='chuanfang_goods'
                p['observations'].append({'business_date':s.get('day'),'fetched_at':s.get('fetched_at'),
                    'spend':row.get('ad_spend'),'gmv':row.get('gmv'),'paid_orders':row.get('order_count'),
                    'complete':False,'evidence':ref,'note':'累计快照不是完整日终证明'})
        status=None;hours=[]
        try:
            with self.database('hourly.db') as db:
                row=db.execute('SELECT payload FROM plan_lists WHERE account=?',(account,)).fetchone();status=json.loads(row[0]) if row else None
                hours=db.execute('SELECT start,payload FROM hours WHERE account=? AND start>=? AND start<? ORDER BY start',(account,since,cutoff)).fetchall()
        except (FileNotFoundError,sqlite3.DatabaseError):gaps.append('hourly_store_unavailable')
        if status:
            at=status.get('fetched_at')
            if not at or datetime.fromisoformat(at)>now:gaps.append('status_future_or_invalid');status=None
        if status:
            for row in status.get('plans',[]):
                p=plan(str(row['plan_id']));p['status']={'status':row.get('status'),'opt_status':row.get('opt_status'),'fetched_at':status['fetched_at'],'scope':status.get('scope')}
        for start,payload in hours:
            s=json.loads(payload)
            if s.get('advertiser_id')!=account or s.get('monetary_unit')!='CNY':gaps.append('hourly_identity_or_unit_invalid');continue
            if s.get('end','')>cutoff or s.get('fetched_at','')>cutoff:continue
            refs.append({'ref':'hourly.db:hours:'+start,'sha256':hashlib.sha256(payload.encode()).hexdigest()})
            for row in s.get('rows',[]):
                p=plan(str(row['plan_id']))
                p['hourly_observations'].append({'start':s['start'],'end':s['end'],'spend':row.get('ad_spend'),'gmv':row.get('gmv'),'paid_orders':None,'evidence':'hourly.db:hours:'+start})
        balances=None
        if balance:
            s=json.loads(balance[1])
            if s.get('advertiser_id')==account:
                balances={'fetched_at':s.get('fetched_at'),'amounts_yuan':{k:s.get('amounts_yuan',{}).get(k) for k in ('account_total','account_valid','account_frozen')},'freshness_verified':False}
                refs.append({'ref':'monitor.db:balances:'+balance[0],'sha256':hashlib.sha256(balance[1].encode()).hexdigest()})
        budgets={}
        folder=self.inside('data/qianchuan')
        for file in folder.glob('budgets_*.json'):
            if not file.resolve().is_relative_to(self.root):continue
            try:
                raw=file.read_bytes();s=json.loads(raw)
                if s.get('advertiser_id')!=account or s.get('kind')!='current_budget':continue
                stamp=s.get('fetched_at','')
                if not stamp or datetime.fromisoformat(stamp)>now:continue
                for row in s.get('plans',[]):
                    pid=str(row['plan_id'])
                    if pid not in budgets or datetime.fromisoformat(stamp)>datetime.fromisoformat(budgets[pid]['snapshot_at']):
                        budgets[pid]={k:row.get(k) for k in ('amount','mode','status','fetched_at')}
                        budgets[pid].update(snapshot_at=stamp,evidence=file.name,sha256=hashlib.sha256(raw).hexdigest(),baseline=None,freshness_verified=False)
            except (ValueError,KeyError,TypeError,OSError):gaps.append('budget_file_invalid')
        model={}
        try:model=json.loads(self.inside('config/rules.json').read_text()).get('model',{})
        except (OSError,ValueError):gaps.append('model_config_unavailable')
        for pid,p in plans.items():
            p['budget']=budgets.get(pid);profile=model.get('plans',{}).get(pid,{})
            band=profile.get('refund_band');rate=model.get('refund_rates',{}).get(model.get('season'),{}).get(band)
            p['refund']={'band':band,'rate':str(rate) if rate is not None else None,'model_version':model.get('version'),
                'season':model.get('season'),'basis':profile.get('basis'),'verified':False,
                'note':'沿用配置值；价格档映射证据未接入，未认定已核验'}
        result={'contract_version':'shadow-data-v1','account_id':account,'as_of':cutoff,'currency':'CNY',
            'production_execution':False,'rule_live_on':None,'source_root':str(self.root),'source_fingerprint':self.fingerprint(),
            'plans':[plans[k] for k in sorted(plans)],'balance':balances,'evidence':refs,'gaps':sorted(set(gaps)),
            'scope_note':'经营为乘方商品快照，状态为商品视频全域；列表并集不是完整经营覆盖。日事实未获得完整性证明。'}

        if self.history_path:
            from .history_source import enrich
            return enrich(result,self.history_path)
        return result
