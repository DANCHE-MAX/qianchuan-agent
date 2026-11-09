"""Read-only projections over completed daily bundles; never contacts ad or chat APIs."""
from datetime import date,timedelta,datetime
from decimal import Decimal
from pathlib import Path
import hashlib,json,sqlite3
from . import profit_policy,decision_brief,historical_evidence

class HistoryUnavailable(ValueError):pass


def quality(row):
 if any(row.get(k) is None for k in ('gmv','spend','profit')):return '待核验'
 return '含业务补值' if row.get('gmv_source')=='business_rule_zero_spend' else '数据齐全'

def groups(report,mapping=None):
 lookup={}
 if mapping:
  if mapping['day']!=report['date'] or str(mapping['account'])!=str(report['account']):raise ValueError('Mapping scope mismatch')
  for r in mapping['rows']:
   value=(r.get('product'),r.get('name'))
   if r['plan'] in lookup and lookup[r['plan']]!=value:raise ValueError('Mapping conflict')
   lookup[r['plan']]=value
 result={};seen=set()
 for row in report['rows']:
  pid=str(row['plan_id'])
  if pid in seen:raise ValueError('Duplicate plan')
  seen.add(pid);product,name=lookup.get(pid,(None,None));managed='全店托管' in str(row.get('plan_name') or row.get('name',''))
  key='managed' if managed else 'product:'+str(product) if product and name else 'plan:'+pid
  label='全店托管' if managed else name if product and name else (row.get('name') or pid)+'（计划，归属待核验）'
  g=result.setdefault(key,dict(id=key,name=label,gmv=Decimal(0),spend=Decimal(0),profit=Decimal(0),plans=[],imputed=0,quality='数据齐全'))
  g['plans'].append(dict(row,quality=quality(row)));g['imputed']+=row.get('gmv_source')=='business_rule_zero_spend'
  for k in ('gmv','spend','profit'):
   value=row.get(k);g[k]=None if g[k] is None or value is None else g[k]+Decimal(str(value))
 for g in result.values():
  for k in ('gmv','spend','profit'):g[k]=None if g[k] is None else f'{g[k]:.2f}'
  g['quality']='待核验' if any(g[k] is None for k in ('gmv','spend','profit')) else '含业务补值' if g['imputed'] else '数据齐全'
 return sorted(result.values(),key=lambda g:(g['profit'] is None,-Decimal(g['profit'] or 0),g['id']))

def build_trends(reports,mappings,end_day):
 byday={};accounts=set()
 for r in reports:
  if r['date'] in byday:raise ValueError('Ambiguous report revision')
  byday[r['date']]=r;accounts.add(str(r['account']))
 if len(accounts)>1:raise ValueError('Mixed accounts')
 end=date.fromisoformat(end_day);result=[]
 for i in reversed(range(7)):
  day=str(end-timedelta(days=i));r=byday.get(day)
  if not r:result.append(dict(date=day,gmv=None,spend=None,profit=None,quality='待核验',groups=[],policy=None));continue
  gs=groups(r,mappings.get(day));entry=dict(date=day,quality='数据齐全',groups=gs,policy=r['policy'])
  for k in ('gmv','spend','profit'):
   entry[k]=None if not gs or any(g[k] is None for g in gs) else f'{sum(Decimal(g[k]) for g in gs):.2f}'
  entry['known']={k: (f'{sum(Decimal(g[k]) for g in gs if g[k] is not None):.2f}' if any(g[k] is not None for g in gs) else None) for k in ('gmv','spend','profit')}
  entry['known_count']={k:sum(g[k] is not None for g in gs) for k in ('gmv','spend','profit')}
  entry['quality']='待核验' if any(entry[k] is None for k in ('gmv','spend','profit')) else '含业务补值' if any(g['imputed'] for g in gs) else '数据齐全'
  result.append(entry)
 return result

def _inside(root,p):
 path=Path(p).resolve()
 if not path.is_relative_to(root.resolve()):raise ValueError('Report path outside runtime')
 return path

def completed(root,account,day):
 root=Path(root);dbpath=root/'data/qianchuan/daily_brief.db'
 if not dbpath.exists():return None,None
 with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
  records=db.execute('SELECT payload FROM jobs WHERE account=? AND day=? AND sent=1',(account,day)).fetchall()
 if len(records)!=1:return None,None
 bundle=json.loads(records[0][0]);expected=bundle.get('input_hashes',{}).get('result.json');retrospective=False
 if expected:
  directory=_inside(root,bundle['directory']);path=directory/'result.json'
  if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Completed report hash changed')
 else:
  proof=historical_evidence.load(root,account,day,bundle)
  if not proof:
   reason='仅有旧版文字日报，未找到对应已发逐计划利润报表及政策' if not bundle.get('directory') else '旧版报表未保存冻结哈希，尚未完成附件回读补验证'
   raise HistoryUnavailable(reason)
  directory=proof['directory'];path=directory/'result.json';retrospective=True
 report=json.loads(path.read_text())
 if retrospective:report['_history_evidence']='附件回读与Excel核对通过的历史补验证，非原始冻结记录'
 if report['date']!=day or str(report['account'])!=str(account):raise ValueError('Report scope mismatch')
 mapping=None;mp=directory/'two_day/collected.json'
 if mp.exists():
  expected_mapping=bundle.get('input_hashes',{}).get('two_day/collected.json')
  if not expected_mapping or hashlib.sha256(mp.read_bytes()).hexdigest()!=expected_mapping:raise ValueError('Mapping hash mismatch')
  mapping=next((d for d in json.loads(mp.read_text()) if d['day']==day),None)
 return report,mapping

def build(root,account,end_day,shadow_root=None):
 root=Path(root);reports=[];mappings={};warnings=[];end=date.fromisoformat(end_day)
 for i in range(7):
  day=str(end-timedelta(days=i))
  try:r,m=completed(root,account,day)
  except HistoryUnavailable as exc:r,m=None,None;warnings.append(day+'：'+str(exc)+'，待核验')
  except (OSError,ValueError,KeyError,sqlite3.Error) as exc:r,m=None,None;warnings.append(day+'：历史报表完整性证据不足，待核验')
  if r:
   reports.append(r)
   if r.get('_history_evidence'):warnings.append(day+'：已完成历史附件补验证；保留原政策与缺失值，商品映射不足时按计划展示')
  if m:mappings[day]=m
 trends=build_trends(reports,mappings,end_day);current=next((r for r in reports if r['date']==end_day),None)
 authority=None
 if current:
  try:authority=profit_policy.load_policy(root,account,current['policy'])
  except (OSError,ValueError,KeyError):warnings.append('统一模型授权参数待核验')
 comparison=[]
 if current and authority:
  # A projection, never a rewrite of the frozen historical baseline.
  for row in current['rows']:
   p=profit_policy.resolve_policy(authority,row['plan_id']);v=profit_policy.estimate(row.get('gmv'),row.get('spend'),p)
   comparison.append(dict(plan_id=row['plan_id'],name=row['name'],historical_profit=row.get('profit'),unified_profit=v['profit'],difference=None if v['profit'] is None or row.get('profit') is None else str(Decimal(v['profit'])-Decimal(row['profit'])),policy=p,quality=quality(row)))
 incidents=[];dbpath=root/'data/qianchuan/alert_lifecycle.db'
 if dbpath.exists():
  with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
   incidents=[json.loads(r[0]) for r in db.execute('SELECT payload FROM incident_state WHERE account=?',(account,))]
 incidents.sort(key=lambda r:(r['status'] not in ('active','unknown'),r['severity']!='urgent',r['kind'],r['subject']))
 # Only expose sanitized, narrowly selected shadow fields; no raw settings or identity configuration.
 shadow=[]
 if shadow_root:
  try:
   src=json.loads((Path(shadow_root)/'evidence/roi/latest.json').read_text())
   for p in src['plans']:
    if str(p.get('account_id',''))!=str(account):
     warnings.append('影子账户范围不符，相关记录待核验');continue
    w=p.get('windows',{}).get('1',{})
    if w.get('start')!=end_day or w.get('end')!=end_day:continue
    shadow.append(dict(plan_id=p['plan_id'],name=p.get('name'),profit=w.get('estimated_profit'),gmv=w.get('gmv'),spend=w.get('spend'),policy=p.get('profit_policy'),refund_rate=p.get('refund_model',{}).get('rate')))
  except (OSError,ValueError,KeyError):warnings.append('对应日期影子数据待核验')
 return dict(end_day=end_day,generated_at=datetime.now().astimezone().isoformat(),trends=trends,comparison=comparison,incidents=incidents,shadow=shadow,warnings=warnings,policy=authority,answers={k:decision_brief.answer(q,dict(current,_decision_mapping=mappings.get(end_day))) if current else '对应日期无完整已发日报，待核验' for k,q in [('profit','昨天哪个商品赚得最多'),('loss','昨天哪个亏损最多'),('spend','昨天广告费花在哪里')]},note='历史原值不改写；统一口径为本地预览。数据齐全不等于真实结算利润。反馈暂停，不自动调整预算、ROI或计划状态。')

def attachments(root,account,day):
 root=Path(root);dbpath=root/'data/qianchuan/daily_brief.db'
 if not dbpath.exists():return []
 with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
  rows=db.execute('SELECT payload FROM jobs WHERE account=? AND day=? AND sent=1',(account,day)).fetchall()
 if len(rows)!=1:return []
 bundle=json.loads(rows[0][0])
 if not bundle.get('input_hashes',{}).get('result.json'):
  proof=historical_evidence.load(root,account,day,bundle)
  if proof:return proof['artifacts']
  return []
 directory=_inside(root,bundle['directory']);result=[]
 if bundle.get('date')!=day or str(bundle.get('account'))!=str(account):raise ValueError('Attachment scope mismatch')
 for item in bundle.get('files',[]):
  path=_inside(root,item['path'])
  if not path.is_relative_to(directory) or path.suffix not in ('.png','.xlsx'):raise ValueError('Attachment outside bundle')
  if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Attachment hash mismatch')
  result.append({'path':path,'name':path.name,'kind':item['kind']})
 return result
