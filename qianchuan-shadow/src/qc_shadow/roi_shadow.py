"""Explain plan delivery and record ROI-target hypotheses; never execute ROI changes."""
from datetime import datetime,timedelta
from decimal import Decimal,localcontext
from zoneinfo import ZoneInfo
from .daily_rules import assess_daily
from .profit_policy import estimate,shadow_policy
D=Decimal;CHINA=ZoneInfo('Asia/Shanghai')

def instant(s):
 t=datetime.fromisoformat(s)
 if not t.tzinfo:raise ValueError('timezone_required')
 return t.astimezone(CHINA)
def number(v):
 if v is None or isinstance(v,bool):return None
 try:
  x=D(str(v));return x if x.is_finite() and x>=0 else None
 except Exception:return None

def analyze_plan(plan,settings,as_of):
 with localcontext() as ctx:
  ctx.prec=40;return _analyze(plan,settings,as_of)

def _analyze(p,s,as_of):
 now=instant(as_of);end=now.date()-timedelta(days=1);gaps=[];rows=p.get('daily',[]);daily={r['day']:r for r in rows}
 duplicate=len(daily)!=len(rows);refund=p.get('refund') or {};rate=number(refund.get('rate'))
 policy=shadow_policy(refund) if refund.get('rate') is not None and refund.get('band') else None
 model=refund.get('verified') is True and rate is not None and rate<1 and bool(refund.get('band') and refund.get('model_version'))
 if not model:gaps.append('refund_model_unverified')
 def window(n):
  dates=[str(end-timedelta(days=i)) for i in reversed(range(n))];missing=[];facts=[]
  for day in dates:
   r=daily.get(day)
   if not r or r.get('complete') is not True or not r.get('evidence') or r.get('daily_aggregate_matches') is not True or any(number(r.get(k)) is None for k in ('spend','gmv','orders')):missing.append(day)
   else:facts.append(r)
  complete=not missing and not duplicate
  value={'start':dates[0],'end':dates[-1],'complete':complete,'available_days':len(facts),'missing_days':missing,'spend':None,'gmv':None,'orders':None,'payment_roi':None,'estimated_profit':None}
  if complete:
   spend=sum((number(r['spend']) for r in facts),D(0));gmv=sum((number(r['gmv']) for r in facts),D(0));orders=sum(int(r['orders']) for r in facts)
   value.update(spend=str(spend),gmv=str(gmv),orders=orders,payment_roi=str(gmv/spend) if spend else None,
    estimated_profit=str(sum((D(estimate(r['gmv'],r['spend'],policy)['profit']) for r in facts),D(0))) if model else None)
  return value
 windows={str(n):window(n) for n in (1,3,7)};quality=assess_daily(rows,as_of,refund,estimator=(lambda g,s:D(estimate(g,s,policy)['profit'])) if model else None)
 fresh=False
 try:fresh=s.get('status')=='ok' and s.get('plan_id')==p['plan_id'] and 0<=(now-instant(s['fetched_at'])).total_seconds()<=45*60
 except (KeyError,ValueError,TypeError):pass
 target=number(s.get('target_roi')) if fresh else None;budget=number(s.get('budget')) if fresh else None
 if target is not None and target<=0:target=None
 if not fresh:gaps.append('settings_unavailable_or_stale')
 flags=s.get('overall_roi_cost_items');switches=(s.get('alliance_commision_switch'),s.get('star_task_material_switch'))
 exclude_star=p.get('star_cost_excluded') is True
 expected=set()
 if switches[0]=='ON':expected.add(4)
 if switches[1]=='ON' and not exclude_star:expected.add(3)
 consistent=isinstance(flags,list) and all(isinstance(v,int) and not isinstance(v,bool) for v in flags) and all(x in ('ON','OFF') for x in (switches[:1] if exclude_star else switches)) and (set(flags)-({3} if exclude_star else set()))==expected
 if not consistent:gaps.append('roi_cost_flags_conflict')
 # The existing approved profit model excludes creator commissions. Do not turn a
 # model profit into permission to lower a goal when additional costs are enabled.
 cost_verified=fresh and consistent and not expected
 if expected:gaps.append('additional_marketing_costs_not_in_profit_model')
 expected_products=refund.get('expected_product_ids')
 if expected_products and refund.get('band')!='overall' and sorted(expected_products)!=sorted(s.get('product_ids',[])):
  gaps.append('product_binding_changed');cost_verified=False
 observations=[]
 for r in p.get('observations',[]):
  try:
   at=instant(r['fetched_at'])
   if r.get('business_date')==str(now.date()) and at<=now:observations.append((at,r))
  except (KeyError,ValueError,TypeError):continue
 observations.sort(key=lambda x:x[0]);intra={'at':None,'spend':None,'gmv':None,'orders':None,'estimated_profit':None,'spend_rate_per_hour':None,'budget_ratio':None,'fresh':False,'note':'日内累计仅供观察，未结算；余额下降与计划消耗分别核算'}
 if observations:
  at,last=observations[-1];spend=number(last.get('spend'));gmv=number(last.get('gmv'));orders=number(last.get('paid_orders'));isfresh=(now-at).total_seconds()<=45*60
  intra.update(at=at.isoformat(),spend=str(spend) if spend is not None else None,gmv=str(gmv) if gmv is not None else None,orders=str(orders) if orders is not None else None,fresh=isfresh,
   estimated_profit=estimate(gmv,spend,policy)['profit'] if model and spend is not None and gmv is not None else None,
   budget_ratio=str(spend/budget) if isfresh and spend is not None and budget else None)
  if len(observations)>=2 and isfresh:
   before,prev=observations[-2];elapsed=D(str((at-before).total_seconds()))/3600;old=number(prev.get('spend'))
   if old is not None and spend is not None and spend>=old and 0<elapsed<=D(2)/3:intra['spend_rate_per_hour']=str((spend-old)/elapsed)
   else:gaps.append('intraday_rate_gap_or_revision')
 else:gaps.append('intraday_row_missing_not_zero')
 last=windows['1'];profit=number(last['estimated_profit']) if last['estimated_profit'] is not None else None
 # number() intentionally excludes negatives; explicit Decimal retains a known loss.
 signed_profit=D(last['estimated_profit']) if last['estimated_profit'] is not None else None
 advice='hold_observe';candidate=None;reasons=[];hypotheses=[]
 if target is None:advice='hold_unverified';reasons.append('目标未取得，不生成调整数值')
 elif s.get('opt_status')!='ENABLE' or s.get('platform_status') not in ('DELIVERY_OK','PRE_OFFLINE_BUDGET','OFFLINE_BUDGET'):
  advice='investigate_platform_status';reasons.append('先核计划状态或投放限制；不自动启停')
 elif s.get('platform_status') in ('PRE_OFFLINE_BUDGET','OFFLINE_BUDGET') or (intra['budget_ratio'] is not None and D(intra['budget_ratio'])>=D('.9')):
  advice='budget_path_only';reasons.append('预算受限候选进入既有预算规则；不靠降低ROI目标处理')
 elif not last['complete'] or signed_profit is None:
  advice='observe_data_gap';reasons.append('昨日完整事实或利润依据不足，先补数据')
 elif signed_profit<0:
  advice='do_not_lower_loss';reasons.append('昨日预计亏损；当前不建议用更宽松目标换量。维持目标并核验成本、成交质量')
 elif last['orders']==0:
  advice='observe_no_sales';reasons.append('昨日无成交；低消耗/样本不足与真实效率差尚不能区分')
 elif (quality.get('Q-STABLE')=='met' and windows['7']['complete']) or (quality.get('Q-GROW')=='met' and window(2)['complete']):
  reasons.append('自身完整日质量条件通过；继续核对消耗趋势、成交稳定性和实际成本，不据此生成ROI调整数值')
 else:reasons.append('自身连续完整日质量证据不足，维持配置并观察')
 if intra['budget_ratio'] is not None and D(intra['budget_ratio'])<D('.9'):reasons.append('当前采样未接近日预算门槛；不把早盘进度外推为全天结果')
 hypotheses.append('缺少经核验的展现/点击/转化漏斗，不能归因于人群、素材或时段；本模块不采素材')
 return {'plan_id':p['plan_id'],'name':s.get('name'),'as_of':as_of,'windows':windows,'intraday':intra,'quality':quality,
  'current_target_roi':str(target) if target is not None else None,
  'current_budget':str(budget) if budget is not None else None,'cost_context_verified':cost_verified,
  'payment_roi_break_even':str(1/((1-rate)*D('.5565'))) if model else None,'break_even_scope':'支付GMV/广告消耗的模型盈亏线，不能直接作为乘方目标ROI或实际结算利润',
  'advice':advice,'candidate_roi':str(candidate) if candidate is not None else None,'reasons':reasons,'hypotheses':hypotheses,'gaps':gaps,
  'settings_evidence':s.get('evidence'),'refund_model':refund,'profit_policy':policy,'shadow_only':True,'roi_write_enabled':False,'plan_pause_enabled':False,
  'budget_policy_changed':False,'causal_validation':'未实施ROI调整；不将后续自然涨跌计为建议准确率'}
