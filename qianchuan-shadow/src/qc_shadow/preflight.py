"""Derive execution gates from pinned daily evidence and fresh official GET results."""
from datetime import date,datetime,timedelta
from decimal import Decimal,localcontext
from zoneinfo import ZoneInfo
from .daily_rules import assess_daily
from .history import amount
from .collection_scope import decision_start,scoped_lifecycle,ACCOUNT
CHINA=ZoneInfo('Asia/Shanghai')
def stamp(v):
 d=datetime.fromisoformat(v)
 if not d.tzinfo:raise ValueError('timezone_required')
 return d.astimezone(CHINA)
def build_preflight(snapshot,live,activation,now):
 with localcontext() as ctx:
  ctx.prec=40
  return _build_preflight(snapshot,live,activation,now)
def _build_preflight(snapshot,live,activation,now):
 now=stamp(now);start=stamp(live['started_at']);end=stamp(live['at']);yesterday=now.date()-timedelta(days=1)
 if not (0<=(now-start).total_seconds()<=60 and start<=end<=now):raise ValueError('live_evidence_stale_or_future')
 if (snapshot['account_id']!=live['account_id'] or stamp(snapshot['as_of']).date()!=now.date() or stamp(snapshot['as_of'])>now):raise ValueError('snapshot_identity_or_date')
 plans=[p for p in snapshot['plans'] if p['plan_id']==live['plan_id']]
 if len(plans)!=1:raise ValueError('plan_missing_or_duplicate')
 p=plans[0];detail=live['detail'];setting=detail.get('delivery_setting') or {};refund=p.get('refund') or {}
 current=amount(setting.get('budget'));available=Decimal(str(live.get('available')))
 if not available.is_finite():raise ValueError('balance_invalid')
 rows=p.get('daily',[]);days={r['day']:r for r in rows};first=p.get('first_enabled_on')
 required=[]
 window_start=decision_start(snapshot['account_id'],first)
 if window_start:
  first=date.fromisoformat(window_start)
  required=[str(first+timedelta(days=i)) for i in range(max(0,(yesterday-first).days+1))]
 complete=bool(required) and len(days)==len(rows) and all(day in days and days[day].get('complete') is True and days[day].get('evidence') and all(days[day].get(k) is not None for k in ('spend','gmv','orders')) for day in required)
 quality=assess_daily(rows,now.isoformat(),refund)
 expected_products=refund.get('expected_product_ids') or []
 actual_products=[str(x['product_id']) for x in detail.get('product_infos',[]) if x.get('product_id') is not None]
 binding_ok=refund.get('band')=='overall' or (len(expected_products)==1 and sorted(actual_products)==sorted(expected_products))
 logs_ok=live.get('logs_complete') is True and date.fromisoformat(live['log_start'])<=yesterday-timedelta(days=1)
 # Any recent plan operation conservatively restarts observation; no guessed actor.
 # The endpoint is already filtered to this plan. Any returned operation (including
 # nested/unknown content) means a clean observation window is not proved.
 recent_ops=live.get('recent_logs')
 if not isinstance(recent_ops,list):logs_ok=False;recent_ops=[{}]
 known_budget=(p.get('budget') or {}).get('amount')
 policy=activation.get('historical_policy')=='replay_only_current_decisions'
 daily_current=str(yesterday) in days and now.hour>=10
 spend=amount(live['today_spend']) if live.get('today_spend') is not None else None
 checks={
 'authorized':activation.get('authorized') is True and activation.get('account_id')==snapshot['account_id'] and p['plan_id'] in activation.get('plans',[]),
 'daily_current':daily_current,
 'daily_reconciled':bool(required) and all(days.get(day,{}).get('daily_aggregate_matches') is True for day in required),
 'revisions_reviewed':not snapshot.get('pending_execution_revisions'),
 'lifecycle_verified':bool(complete and (p.get('first_enabled_evidence') or snapshot['account_id']==ACCOUNT) and policy),
 'refund_verified':refund.get('verified') is True and bool(refund.get('verification_evidence')) and binding_ok,
 'quality_met':quality.get('Q-GROW')=='met' or quality.get('Q-STABLE')=='met',
 'yesterday_profitable':quality.get('last_day_profitable') is True,
 'budget_constrained':bool((spend is not None and spend>=current*Decimal('.9')) or detail.get('status') in ('OFFLINE_BUDGET','PRE_OFFLINE_BUDGET')),
 'balance_above_500':available>500,
 'platform_active':detail.get('opt_status')=='ENABLE' and detail.get('status') in ('DELIVERY_OK','OFFLINE_BUDGET','PRE_OFFLINE_BUDGET'),
 'budget_unchanged':known_budget is not None and amount(known_budget)==current and setting.get('budget_mode')=='BUDGET_MODE_DAY',
 'observation_complete':bool(logs_ok and not recent_ops),
 'no_protection':activation.get('no_prior_protection_or_recovery') is True,
 'scope_verified':p.get('scope')=='chuanfang_goods' and str(detail.get('ad_id'))==p['plan_id'] and detail.get('marketing_goal')=='VIDEO_PROM_GOODS' and detail.get('adlab_scene')==1,
 }
 lifecycle={'stage':'unverified','budget_vetoes':['history_or_refund_incomplete'],'events':[]}
 if complete and refund.get('verified') is True:
  from .lifecycle_replay import replay
  lifecycle=scoped_lifecycle(snapshot['account_id'],rows,p.get('first_enabled_on'),yesterday,refund['rate'],activation['live_on'])
  checks['lifecycle_verified']=checks['lifecycle_verified'] and not lifecycle['budget_vetoes']
  checks['no_protection']=checks['no_protection'] and 'current_or_pending_protection' not in lifecycle['budget_vetoes']
 # Match the rule engine: a cumulative unknown or exactly break-even history cannot increase.
 if complete and refund.get('verified') is True:
  profit=sum((amount(days[d]['gmv'])*(1-amount(refund['rate']))*Decimal('.5566')-amount(days[d]['spend']) for d in required),Decimal(0))
  checks['lifecycle_verified']=checks['lifecycle_verified'] and profit!=0
 return {'account_id':snapshot['account_id'],'plan_id':p['plan_id'],'at':start.isoformat(),'current':str(current),
  'checks':checks,'evidence':live['evidence'],'quality':quality,'lifecycle':lifecycle,'blocking_checks':[k for k,v in checks.items() if not v]}
