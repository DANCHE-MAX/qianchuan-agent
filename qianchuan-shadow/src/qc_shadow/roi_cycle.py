"""Independent ROI shadow journal. No budget, pause or ROI mutation capability."""
import json,hashlib,csv,copy
from pathlib import Path
from .roi_source import acquire,clock
from .roi_shadow import analyze_plan
from .collection_scope import ACCOUNT,START
from .creator_cost import annotate
from .daily_refresh import atomic
from .plan_audit import audit_plan,revision_packets,diagnose,apply_recovery,revision_id
from .gap_recovery import ensure as recovery_ensure
from .revision_review import save_queue
from .commerce_evidence import ensure as commerce_ensure,attach as commerce_attach
LABELS={'hold_observe':'维持目标，继续观察','hold_unverified':'目标未核验','investigate_platform_status':'先核平台投放状态','budget_path_only':'预算受限，沿用原预算门禁','observe_data_gap':'完整日报缺项','do_not_lower_loss':'预计亏损，不建议降目标','observe_no_sales':'无成交，样本不足','consider_lower_shadow':'仅影子候选，待验证'}
def run_cycle(home,source,snapshots):
 home=Path(home);home.mkdir(parents=True,exist_ok=True);outputs=[];errors=[];inputs=[];review_items=[]
 for snapshot in snapshots:
  plans=[p for p in snapshot['plans'] if p.get('scope')=='chuanfang_goods']
  if not plans:continue
  settings=acquire(home/'settings',source,snapshot['account_id'],[p['plan_id'] for p in plans]);at=clock()
  commerce=commerce_ensure(home/'commerce',source,snapshot['account_id'])
  if commerce['status']!='ok':errors.append({'component':'commerce','code':commerce.get('code'),'error_type':commerce.get('error_type')})
  recovery=recovery_ensure(home/'recovery',source,snapshot)
  if recovery['status']!='ok':errors.append({'component':'daily_recovery','code':recovery.get('code'),'error_type':recovery.get('error_type')})
  revisions=snapshot.get('pending_execution_revisions',[])
  reviewed={revision_id(x['revision']):x for x in snapshot.get('reviewed_execution_revisions',[])}
  packets=revision_packets(revisions+[x['revision'] for x in reviewed.values()],[dict(row,plan_id=p['plan_id']) for p in plans for row in p.get('daily',[])])
  for packet in packets:
   if packet['revision_id'] in reviewed:packet['review_resolution']=reviewed[packet['revision_id']]
  review_items+=packets
  mapped={s['plan_id']:s for s in settings['settings']};inputs.append({'snapshot':snapshot,'settings':settings,'commerce_evidence':commerce.get('evidence'),'commerce_status':commerce['status']})
  for p in plans:
   s=mapped[p['plan_id']];extra=recovery.get('plans',{}).get(p['plan_id'],[]);analysis_plan=apply_recovery(p,extra)
   if snapshot['account_id']==ACCOUNT:analysis_plan['star_cost_excluded']=True
   from .profit_policy import load_policy,resolve_policy
   authority=home.parent.parent/'data/model_policy/authority.json'
   if authority.exists():
    original_refund=analysis_plan.get('refund',{})
    if original_refund.get('rate') is not None:
     policy=load_policy(home.parent.parent,snapshot['account_id'],{'refund_rate':json.loads(authority.read_text())['overall_rate'],'ad_rate':'.15'})
     resolved=resolve_policy(policy,p['plan_id'])
     analysis_plan=copy.deepcopy(analysis_plan)
     analysis_plan['refund'].update(rate=resolved['refund_rate'],band=resolved['refund_band'],model_version=resolved['model_version'],verification_evidence='shared_local_authority:'+policy['authority_version'])
   r=analyze_plan(analysis_plan,s,at);r['account_id']=snapshot['account_id']
   if snapshot['account_id']==ACCOUNT:annotate(r)
   r['data_audit']=audit_plan(p,at,revisions,{'status':recovery['status'],'attempts':extra},collection_start=str(START) if snapshot['account_id']==ACCOUNT else None)
   r['diagnosis']=diagnose(r,s)
   r['commerce']=commerce_attach(p,commerce,at[:10])
   if r['commerce']['status']=='revision_conflict':
    r['gaps'].append('commerce_base_revision_conflict');r['candidate_roi']=None
    errors.append({'component':'commerce_revision','plan_id':p['plan_id'],'days':r['commerce']['base_revision_days']})
    if r['advice']=='consider_lower_shadow':r['advice']='hold_observe'
   if snapshot.get('pending_execution_revisions'):
    r['gaps'].append('daily_revision_review_pending');r['candidate_roi']=None
    if r['advice']=='consider_lower_shadow':r['advice']='hold_observe'
   if s['status']!='ok':errors.append({'plan_id':p['plan_id'],'code':s.get('code'),'http':s.get('http'),'request_id':s.get('request_id'),'error_type':s.get('error_type')})
   # Same closed facts and settings remain the same case, not a new learning day.
   basis={'account':snapshot['account_id'],'plan':p['plan_id'],'windows':r['windows'],'refund':p.get('refund'),'target':r['current_target_roi'],'gaps':sorted(r['gaps']),'commerce':r['commerce'].get('days',[])}
   r['case_id']=hashlib.sha256(json.dumps(basis,sort_keys=True).encode()).hexdigest();outputs.append(r)
 review_items=save_queue(home/'review',home.parent/'daily',review_items)
 report={'at':clock(),'status':'partial' if errors else 'ok','shadow_only':True,'roi_write_enabled':False,'plan_pause_enabled':False,'budget_policy_changed':False,'plans':outputs,'errors':errors,'revision_review':review_items}
 raw=json.dumps({'input':inputs,'output':report},ensure_ascii=False,sort_keys=True).encode();key=hashlib.sha256(raw).hexdigest();runs=home/'runs';runs.mkdir(exist_ok=True)
 (runs/(key+'.json')).write_bytes(raw);report['run_id']=key;atomic(home/'latest.json',report)
 lines=['# 计划跑量与ROI影子分析','',report['at'],'','星选成本按用户确认排除。用户确认本账号当前商单均为自营，达人佣金按0元处理，不再按固定5%推算；预计利润仍为退款模型估算，非实际结算利润。未修改ROI或启停计划。相同日数据不作为新学习样本。仅按本计划自身数据观察，不采集或使用平台建议ROI。','', '|计划|当前目标ROI|昨日消耗|昨日订单|昨日预计利润|建议|','|---|---:|---:|---:|---:|---|']
 def show(v):return '待核验' if v is None else str(v)
 for r in outputs:
  d=r['windows']['1'];lines.append('|'+ '|'.join(show(x).replace('|','/') for x in [r['name'] or r['plan_id'],r['current_target_roi'],d['spend'],d['orders'],d['estimated_profit'],LABELS[r['advice']]])+'|')
 for r in outputs:
  recent=r['commerce'].get('days',[]);yesterday=r['windows']['1']['end'];fact=next((d for d in recent if d['day']==yesterday),None)
  if fact:lines+=['', '净成交补采：'+(r['name'] or r['plan_id'])+'，昨日1小时净成交 '+show(fact['net_gmv_1h'])+' 元 / '+show(fact['net_orders_1h'])+' 单；滚动结算并非最终利润。']
 for r in outputs:lines+=['','## '+(r['name'] or r['plan_id']),'；'.join(r['reasons']),'待验证：'+'；'.join(r['hypotheses']),'缺项：'+', '.join(r['gaps'])]
 temp=home/'latest.md.tmp';temp.write_text('\n'.join(lines));temp.replace(home/'latest.md')
 write_plan_audit(home,report)
 return {'status':report['status'],'plan_count':len(outputs),'error_count':len(errors),'run_id':key,'roi_write_enabled':False,'report':str(home/'latest.md'),'plan_audit':str(home/'plan-audit.md')}


def write_plan_audit(home,report):
 lines=['# 逐计划数据缺项与跑量诊断','',report['at'],'','事实与因果分开：低消耗不等于ROI目标过高；利润均为退款模型估算；用户确认本账号当前自营商单无达人佣金。补采不清除预算门禁，不改ROI、不暂停、不采素材。','', '|计划|历史缺失日数|近7日缺失|诊断|判断依据层级|','|---|---:|---:|---|---|']
 flat=[]
 for r in report['plans']:
  a=r['data_audit'];d=r['diagnosis'];missing=a['missing_daily_dates'];n='范围未知' if missing is None else str(len(missing))
  lines.append('|'+ '|'.join(str(v).replace('|','/') for v in [r['name'] or r['plan_id'],n,len(a['recent_missing_dates']),d['label'],d['causal_confidence']])+'|')
  attempts={x['day']:x for x in a['recovery'].get('attempts',[])}
  for day in missing if missing is not None else a['recent_missing_dates']:
   x=attempts.get(day,{});flat.append({'plan_id':r['plan_id'],'name':r['name'],'day':day,'gap_type':next((g['reason'] for g in a['date_gaps'] if g['day']==day),'first_enabled_unknown_recent_gap'),'recovery_status':x.get('status',a['recovery'].get('status','not_attempted')),'evidence':x.get('evidence',''),'budget_clearance':False})
 for r in report['plans']:
  a=r['data_audit'];d=r['diagnosis'];v=r['windows']['1'];attempts=a['recovery'].get('attempts',[]);counts={}
  for x in attempts:counts[x['status']]=counts.get(x['status'],0)+1
  lines+=['','## '+(r['name'] or r['plan_id'])+'（'+r['plan_id']+'）','本轮采集起点：'+str(a.get('collection_start_day') or '沿用原范围')+'；此前证据保留；本账户完整性按用户确认的10月22日起窗口核验。','首次开启：'+str(a['first_enabled_on'] or '待核验，不能以最早报表代替'),
   '缺失日期：'+('完整历史范围未知；近7日缺失 '+', '.join(a['recent_missing_dates']) if a['missing_daily_dates'] is None else (', '.join(a['missing_daily_dates']) or '无')),
   '补采结果：'+json.dumps(counts,ensure_ascii=False)+'；失败不会记为0或未返回。',
   '昨日：消耗 '+str(v['spend'])+' 元，成交 '+str(v['orders'])+' 单，预计利润 '+str(v['estimated_profit'])+' 元（None表示缺失）。',
   '当前观察速率：'+str(d['current_spend_rate_per_hour'])+' 元/小时（未取得有效相邻点则为空）。',
   '诊断：'+d['label']+'；证据层级：'+d['causal_confidence']+'。下一步：'+d['next_step'],
   '尚不能认定：'+'；'.join(d['unknown_causes']),
   '其他缺项：'+'；'.join(a['needed_data']+r['gaps']),
   '修订：本计划 '+str(len(a['own_revision_ids']))+' 项；账户其他计划 '+str(a['other_plan_revision_count'])+' 项。原账户级预算阻断保持不变。']
 lines+=['','## 修订审核']
 for p in report['revision_review']:
  lines+=['','修订ID：'+p['revision_id'],'计划 '+p['plan_id']+' / '+p['day'],'变化：'+json.dumps(p['delta'],ensure_ascii=False),'技术证据齐备：'+str(p['technical_evidence_ready'])+'；业务审核：'+p['review_status']+'；不代表预算执行已获准。','核对清单：'+'；'.join(p['required_review'])]
 temp=home/'plan-audit.md.tmp';temp.write_text('\n'.join(lines));temp.replace(home/'plan-audit.md')
 with (home/'missing-dates.csv.tmp').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['plan_id','name','day','gap_type','recovery_status','evidence','budget_clearance']);w.writeheader();w.writerows(flat)
 (home/'missing-dates.csv.tmp').replace(home/'missing-dates.csv')
