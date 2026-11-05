"""Per-plan missing facts, non-authorizing review packets and causal diagnosis."""
import hashlib,json,copy
from datetime import date,timedelta
from decimal import Decimal
from .roi_shadow import instant,number

def valid_day(r):
 return bool(r.get('complete') is True and r.get('daily_aggregate_matches') is True and r.get('evidence') and all(number(r.get(k)) is not None for k in ('spend','gmv','orders')) and number(r['orders'])==number(r['orders']).to_integral_value())
def revision_id(r):return hashlib.sha256(json.dumps(r,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def revision_packets(revisions,rows):
 indexed={(r['plan_id'],r['day']):r for r in rows};packets=[]
 for r in revisions:
  before=r.get('before') or {};after=r.get('after') or {};current=indexed.get((r['plan_id'],r['day']))
  same=bool(current and after and all(str(current.get(k))==str(after.get(k)) or (number(current.get(k)) is not None and number(after.get(k)) is not None and number(current[k])==number(after[k])) for k in ('spend','gmv','orders')))
  packets.append({'revision_id':revision_id(r),'plan_id':r['plan_id'],'day':r['day'],'before':before,'after':after,'current':current,
   'delta':{k:str(Decimal(str(after[k]))-Decimal(str(before[k]))) if before.get(k) is not None and after.get(k) is not None else None for k in ('spend','gmv','orders')},
   'checks':{'current_matches_after':same,'current_reconciled':bool(current and valid_day(current)),'before_evidence_present':bool(before.get('evidence')),'after_evidence_present':bool(after.get('evidence'))},
   'review_status':'待人审','execution_clearance':False,'required_review':['核对同账户、同计划、同一天及指标口径','核对新旧原始证据与对账结果','确认修订对历史生命周期和预算条件的影响'],
   'note':'技术一致性不等于人工批准；保留原预算阻断，不自动清理修订记录'})
 return packets

def audit_plan(p,as_of,revisions,recovery=None,collection_start=None):
 end=instant(as_of).date()-timedelta(days=1);rows={r['day']:r for r in p.get('daily',[])};counts={}
 for r in p.get('daily',[]):counts[r['day']]=counts.get(r['day'],0)+1
 first=p.get('first_enabled_on');missing=None;gaps=[]
 dates=[str(date.fromisoformat(first)+timedelta(days=i)) for i in range(max(0,(end-date.fromisoformat(first)).days+1))] if first else []
 if collection_start:
  start=max(first,collection_start) if first else collection_start
  dates=[str(date.fromisoformat(start)+timedelta(days=i)) for i in range(max(0,(end-date.fromisoformat(start)).days+1))]
 def reason(day):
  if day not in rows:return 'api_row_absent'
  if counts[day]>1:return 'duplicate_day'
  if rows[day].get('daily_aggregate_matches') is not True:return 'unreconciled'
  return 'incomplete_or_invalid_fields'
 if first or collection_start:
  missing=[d for d in dates if d not in rows or not valid_day(rows[d]) or counts[d]>1]
  for d in missing:gaps.append({'day':d,'reason':reason(d)})
 recent=[str(end-timedelta(days=i)) for i in reversed(range(7)) if str(end-timedelta(days=i)) not in rows or not valid_day(rows[str(end-timedelta(days=i))]) or counts[str(end-timedelta(days=i))]>1]
 if collection_start:recent=[d for d in recent if d>=collection_start]
 local=[r for r in revisions if r['plan_id']==p['plan_id']];other=len(revisions)-len(local)
 return {'collection_start_day':collection_start,'first_enabled_on':first,'first_enabled_evidence':p.get('first_enabled_evidence'),'history_boundary':'verified_first_enable' if first else 'unknown_do_not_infer_from_oldest_row',
  'missing_daily_dates':missing,'date_gaps':gaps,'recent_missing_dates':recent,'own_revision_ids':[revision_id(r) for r in local],
  'account_revision_gate':bool(revisions),'other_plan_revision_count':other,'recovery':recovery or {},'budget_gate_unchanged':True,
  'needed_data':(['首次开启操作日志'] if not first else [])+(['缺失或未对账的完整自然日记录'] if missing or recent else [])+(['本计划历史修订人工审核'] if local else [])+(['账户原修订阻断仍生效'] if other else [])}

def diagnose(r,s):
 d=r['windows']['1'];i=r['intraday'];facts=[];excluded=[];unknown=['展现/点击/转化漏斗未核验，无法归因竞价、人群或时段','缺少可比操作历史和受控验证，不能认定当前ROI目标过高'];confidence='未确认'
 facts.append({'name':'昨日完整日','value':d,'evidence':'validated_daily_window'})
 facts.append({'name':'当前平台状态','value':{'opt_status':s.get('opt_status'),'status':s.get('platform_status')},'evidence':r.get('settings_evidence')})
 if s.get('status')!='ok' or 'settings_unavailable_or_stale' in r['gaps']:category='settings_unknown';label='平台状态未核验';next_step='补取新鲜计划状态，暂不判断限制原因'
 elif s.get('opt_status')!='ENABLE':category='platform_inactive';label='平台返回计划未开启';confidence='已观察';next_step='核验原有关闭原因，保持不自动启停'
 elif s.get('platform_status') in ('OFFLINE_BUDGET','PRE_OFFLINE_BUDGET'):category='platform_budget_limit';label='平台返回预算限制状态';confidence='已观察';next_step='进入原预算门禁核验，不自动降低ROI'
 elif s.get('platform_status')!='DELIVERY_OK':category='platform_restriction';label='平台返回非正常投放状态';confidence='已观察';next_step='按原始状态码核验限制，不猜测审核或余额原因'
 elif i.get('fresh') and i.get('budget_ratio') is not None and Decimal(i['budget_ratio'])>=Decimal('.9'):category='budget_near_limit';label='接近日预算，待核限额影响';confidence='候选';next_step='结合平台状态及原预算条件核验，未证明被预算卡量'
 elif not d['complete']:category='daily_data_gap';label='完整日报缺失，无法判断效率';next_step='定向补采缺失日期，不以0替代'
 elif d['orders']==0:category='no_sales_sample';label='有观测但无成交，尚不能判断效率';next_step='继续观察消耗及成交；极小消耗不能认定跑量能力差'
 elif d.get('estimated_profit') is not None and Decimal(d['estimated_profit'])<0:category='sales_model_loss';label='有成交但模型预计亏损';confidence='模型测算';next_step='核对退款与成本，不建议降ROI换量'
 else:category='sales_observe';label='有成交，继续观察稳定性';confidence='已观察';next_step='核对多日订单与预计利润，单日盈利不等于可放量'
 if i.get('fresh') and i.get('budget_ratio') is not None and Decimal(i['budget_ratio'])<Decimal('.9'):excluded.append('当前采样未接近日预算；不证明全天不会触顶')
 return {'category':category,'label':label,'causal_confidence':confidence,'facts':facts,'not_supported':excluded,'unknown_causes':unknown,'next_step':next_step,
  'current_spend_rate_per_hour':i.get('spend_rate_per_hour'),'rate_is_causal_proof':False,'materials_collected':False,'roi_write_enabled':False,'plan_pause_enabled':False}


def apply_recovery(plan,entries):
 result=copy.deepcopy(plan);rows=result.get('daily',[])
 for entry in entries:
  if entry['status']!='recovered_shadow_only' or not valid_day(entry.get('row',{})):continue
  day=entry['day'];existing=[r for r in rows if r['day']==day]
  if len(existing)==1 and valid_day(existing[0]):continue
  rows=[r for r in rows if r['day']!=day]+[copy.deepcopy(entry['row'])]
 result['daily']=rows;return result
