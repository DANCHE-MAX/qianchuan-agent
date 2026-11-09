"""Local delayed-evidence ledger. No sender, budget operation or report mutation."""
import hashlib,json,sqlite3
from pathlib import Path
from datetime import date
from decimal import Decimal
from . import hourly,profit_daily,plan_profit,data_completeness
from .reliability import file_lock

LOOKBACK_DAYS=7

def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()

class Store:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'data/qianchuan/verification_tracker.db'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS cases(
                id TEXT PRIMARY KEY,account TEXT,day TEXT,plan TEXT,source_hash TEXT,
                original TEXT,created_at TEXT,status TEXT,reason TEXT,comparison TEXT);
                CREATE TABLE IF NOT EXISTS observations(
                case_id TEXT,run_day TEXT,checked_at TEXT,kind TEXT,payload TEXT,
                PRIMARY KEY(case_id,run_day));''')
    def connect(self):return sqlite3.connect(self.path,timeout=10)
    def ingest(self,report,source_hash,now,mapping=None):
        # Calling source loader owns source validation; immutable baseline for each report revision.
        ids=[str(r['plan_id']) for r in report['rows']]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate baseline plans')
        with self.connect() as db:
            for row in report['rows']:
                money_pending=row.get('gmv') is None or row.get('spend') is None or row.get('profit') is None or row.get('gmv_source')=='business_rule_zero_spend'
                map_pending=mapping is not None and mapping.get(str(row['plan_id']),'pending') in ('pending','unlinked')
                if not money_pending and not map_pending:continue
                key=digest(str(report['account'])+':'+report['date']+':'+str(row['plan_id'])+':'+source_hash)
                original={'row':row,'policy':row.get('policy',report['policy']),'money_pending':money_pending,'mapping_pending':map_pending}
                db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (key,str(report['account']),report['date'],str(row['plan_id']),source_hash,encode(original),now.isoformat(),'pending','尚未复查',None))
    def snapshot(self,account):
        with self.connect() as db:
            db.row_factory=sqlite3.Row;cases=[]
            for r in db.execute('SELECT * FROM cases WHERE account=? ORDER BY day,plan,source_hash',(str(account),)):
                item=dict(r);item['original']=json.loads(item['original']);item['comparison']=json.loads(item['comparison']) if item['comparison'] else None
                obs=[dict(x) for x in db.execute('SELECT run_day,checked_at,kind,payload FROM observations WHERE case_id=? ORDER BY run_day',(item['id'],))]
                for x in obs:x['payload']=json.loads(x['payload'])
                item.update(attempts=len(obs),api_attempts=sum(x['kind']=='api' for x in obs),history=obs,last_checked=obs[-1]['checked_at'] if obs else None);cases.append(item)
        return {'account':str(account),'cases':cases,'scope':'按原报表版本跟踪；历史文件与发送账本不变，差额为原政策模型估算。'}
    def record(self,case,now,status,reason,observation=None,comparison=None,kind='api'):
        payload={'status':status,'reason':reason,'observation':observation,'comparison':comparison}
        with self.connect() as db:
            added=db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)',(case['id'],now.date().isoformat(),now.isoformat(),kind,encode(payload))).rowcount
            if added:
                db.execute('UPDATE cases SET status=?,reason=?,comparison=COALESCE(?,comparison) WHERE id=?',
                    (status,reason,encode(comparison) if comparison is not None else None,case['id']))
    def check(self,env,now,request=profit_daily.get):
        now=now.astimezone(hourly.CHINA);account=hourly.account_id(env);groups={}
        for c in self.snapshot(account)['cases']:
            if c['status']=='resolved' or any(h['run_day']==now.date().isoformat() for h in c['history']):continue
            original=c['original'];row=original['row'];age=(now.date()-date.fromisoformat(c['day'])).days
            reason=None
            if not original['money_pending'] or c['comparison'] is not None:reason='金额已核验；历史商品归属缺证据，待人工确认'
            elif row.get('goal')!='VIDEO_PROM_GOODS':reason='直播或未知场景缺少单计划核验维度，待人工核验'
            elif not 1<=age<=LOOKBACK_DAYS:reason='超出最近7个完整自然日自动核验范围；记录保留，待人工核验'
            elif row.get('scene') not in ('OVERALL_PROJECT','UNI_PROJECT'):reason='计划场景缺失，无法选择同口径报表'
            if reason:self.record(c,now,'manual_pending',reason,kind='manual');continue
            groups.setdefault((c['day'],row['scene']),[]).append(c)
        for (day,scene),cases in groups.items():
            topic,cost=next((t,c) for t,c,s in profit_daily.TOPICS if s==scene)
            ids=sorted({c['plan'] for c in cases})
            for offset in range(0,len(ids),1000):
                batch=ids[offset:offset+1000];subset=[c for c in cases if c['plan'] in batch]
                params={'advertiser_id':account,'start_time':day+' 00:00:00','end_time':day+' 23:59:59','data_topic':topic,
                    'dimensions':['ad_id','stat_time_day'],'metrics':[cost,profit_daily.GMV],
                    'filters':[{'field':'ad_id','operator':7,'values':batch}],'order_by':[]}
                folder=self.root/'out/verification_tracker'/account/now.date().isoformat()/'source'/now.strftime('%H%M%S%f')
                try:
                    rows,rids=profit_daily.pages(env,profit_daily.REPORT,params,folder,day+'-'+scene+'-'+str(offset),request)
                    found={}
                    for r in rows:
                        pid=str(r['dimensions']['ad_id']['Value'])
                        if pid not in batch or pid in found or r['dimensions']['stat_time_day']['ValueStr']!=day:raise ValueError('EvidenceScopeConflict')
                        gmv=r['metrics'].get(profit_daily.GMV,{}).get('Value');spend=r['metrics'].get(cost,{}).get('Value')
                        found[pid]={'gmv':str(plan_profit.money(plan_profit.number(gmv))) if gmv is not None else None,
                                    'spend':str(plan_profit.money(plan_profit.number(spend))) if spend is not None else None,
                                    'request_ids':rids,'topic':topic,'date':day}
                    for c in subset:
                        observed=found.get(c['plan'])
                        if observed is None or observed['gmv'] is None or observed['spend'] is None or not rids or not all(rids):
                            self.record(c,now,'pending','API无记录或GMV/消耗/请求证据不完整；不补0、不关闭',observed);continue
                        original=c['original'];row=original['row'];calc=plan_profit.calculate(observed['gmv'],observed['spend'],original['policy'])
                        def difference(new,old):return str(Decimal(new)-Decimal(old)) if old is not None else None
                        comparison={'original_gmv':row.get('gmv'),'original_raw_gmv':row.get('raw_gmv',row.get('gmv')),
                            'original_spend':row.get('spend'),'original_profit':row.get('profit'),'observed_gmv':observed['gmv'],
                            'observed_spend':observed['spend'],'reestimated_profit':calc['profit'],
                            'gmv_difference':difference(observed['gmv'],row.get('gmv')),'spend_difference':difference(observed['spend'],row.get('spend')),
                            'profit_difference':difference(calc['profit'],row.get('profit')),
                            'business_imputed':row.get('gmv_source')=='business_rule_zero_spend','policy':original['policy']}
                        status='manual_pending' if original['mapping_pending'] else 'resolved'
                        self.record(c,now,status,'真实金额已回补；商品历史归属仍待核验' if original['mapping_pending'] else '真实金额完整，已核验关闭；原报表未改写',observed,comparison)
                except Exception as exc:
                    for c in subset:self.record(c,now,'pending','只读查询失败：'+type(exc).__name__+'；保留原值，等待下次核验')
        self.export(account,now)
    def export(self,account,now):
        result=self.snapshot(account);result['checked_at']=now.isoformat()
        result['counts']={s:sum(c['status']==s for c in result['cases']) for s in ('pending','manual_pending','resolved')}
        folder=self.root/'out/verification_tracker'/str(account)/now.date().isoformat();profit_daily.save(folder/'tracker.json',result)
        lines=['# 待核验跟踪与补值对照','仅留本地；不发群、不改历史报表、不调整投放。',result['scope'],
               '状态：'+encode(result['counts']),'同一计划不同历史版本分别保留；数量不等于唯一计划数。']
        for c in result['cases']:
            lines+=['',f"## {c['day']} · {c['original']['row'].get('name','名称待核验')} · {c['plan']}",
                    f"来源版本 {c['source_hash'][:12]}；{c['status']}；检查{c['attempts']}次（API {c['api_attempts']}次）",c['reason']]
            v=c['comparison']
            if v:lines.append('原GMV '+str(v['original_gmv'])+' → 真实GMV '+v['observed_gmv']+'；原模型利润 '+str(v['original_profit'])+' → 复算 '+v['reestimated_profit']+'；利润差额 '+str(v['profit_difference'] if v['profit_difference'] is not None else '待核验')+'元。')
        (folder/'tracker.md').write_text('\n'.join(lines),encoding='utf-8')
        return result


def ingest_sent(root,account,now,store):
    """Read existing completed jobs only, no directory glob as a sent-report substitute."""
    from . import report_preflight
    root=Path(root).resolve();path=root/'data/qianchuan/daily_brief.db';problems=[]
    if not path.exists():return problems
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT day,payload FROM jobs WHERE account=? AND sent=1 ORDER BY day',(account,)).fetchall()
    for day,payload in rows:
        try:
            bundle=json.loads(payload)
            if bundle.get('type')!='profit_bundle':continue
            if str(bundle.get('account'))!=account or bundle.get('date')!=day:raise ValueError('BundleScopeMismatch')
            folder=Path(bundle['directory']).resolve();folder.relative_to(root/'out/profit_daily')
            report_path=folder/'result.json';raw=report_path.read_bytes();report=json.loads(raw)
            receipt=json.loads((folder/'delivery.json').read_text());parts=receipt.get('parts',{})
            if not receipt.get('complete') or len(parts)!=len(bundle['files'])+1 or not all(v.get('verified') and v.get('message_id') for v in parts.values()):raise ValueError('IncompleteReceipt')
            for f in bundle['files']:
                if hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()!=f['sha256']:raise ValueError('ArtifactHashMismatch')
            source_hash=hashlib.sha256(raw).hexdigest();expected=bundle.get('input_hashes',{}).get('result.json')
            if expected and expected!=source_hash:raise ValueError('SourceHashMismatch')
            errors=[];report_preflight.inspect_report(report,account,day,errors,[],'source')
            report_preflight.inspect_excel(folder/('profit-'+day+'.xlsx'),report,errors)
            if errors:raise ValueError('InvalidSourceAmounts')
            days_path=folder/'two_day/collected.json';days=json.loads(days_path.read_text()) if days_path.exists() else []
            bound=bundle.get('input_hashes',{}).get('two_day/collected.json')
            if bound and hashlib.sha256(days_path.read_bytes()).hexdigest()!=bound:raise ValueError('MappingHashMismatch')
            coverage=data_completeness.build(report,next((d for d in days if d.get('day')==day),None))
            store.ingest(report,source_hash,now,{c['plan_id']:c['product_mapping'] for c in coverage['plans']})
        except Exception as exc:problems.append({'date':day,'error':type(exc).__name__,'status':'来源待核验，未自动登记'})
    return problems


def safe_tick(root,env,now):
    """Failure stays local and must never trigger resend/notification or break daily delivery."""
    root=Path(root);folder=root/'out/verification_tracker'
    try:
        if now.tzinfo is None:raise ValueError('TimezoneRequired')
        now=now.astimezone(hourly.CHINA);folder.mkdir(parents=True,exist_ok=True)
        with file_lock(root/'data/qianchuan/verification_tracker.lock'):
            account=hourly.account_id(env);store=Store(root)
            marker=folder/account/now.date().isoformat()/'run.json'
            if marker.exists() and json.loads(marker.read_text()).get('complete'):return
            problems=ingest_sent(root,account,now,store);store.check(env,now)
            profit_daily.save(marker,{'complete':True,'checked_at':now.isoformat(),'source_problems':problems,'mode':'read_only_local'})
    except Exception as exc:
        try:profit_daily.save(folder/'last_error.json',{'checked_at':now.isoformat(),'error':type(exc).__name__,'status':'待核验；未发送消息或改写历史报表'})
        except Exception:pass  # A local audit disk failure must not enter a group-notification path.
