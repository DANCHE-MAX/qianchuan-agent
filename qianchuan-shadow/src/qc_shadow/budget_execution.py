"""Single-plan budget increases only. No retries, pause/resume or other writes.

The preflight callback is a trusted adapter, never a user-submitted dictionary.
Production wiring stays disabled until every required evidence adapter is ready.
"""
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_FLOOR,localcontext
from zoneinfo import ZoneInfo
from .engine import VERSION
REQUIRED=('authorized','daily_current','daily_reconciled','revisions_reviewed','lifecycle_verified',
 'refund_verified','quality_met','yesterday_profitable','budget_constrained','balance_above_500',
 'platform_active','budget_unchanged','observation_complete','no_protection','scope_verified')
CHINA=ZoneInfo('Asia/Shanghai')
class BeforeSubmitBlocked(Exception):
 """Raised only before any platform write request has been submitted."""

class BudgetExecutor:
 def __init__(self,store,allowlist,clock):self.store,self.allowlist,self.clock=store,allowlist,clock
 def execute(self,key,account,plan,preflight,write,readback):
  if plan not in self.allowlist.get(account,set()):return {'status':'blocked','blockers':['scope']}
  b=self.store.baseline(account,plan)
  if not b:return {'status':'blocked','blockers':['fixed_B_missing']}
  # Reserve before preflight: same plan cannot race across workers/processes.
  if not self.store.reserve(key,account,plan,{'baseline':b,'rule_version':VERSION}):return {'status':'duplicate_or_uncertain'}
  def stop(status,**details):
   self.store.finish(key,'not_sent',dict(status=status,**details));return dict(status=status,**details)
  try:
   facts=preflight();now=datetime.fromisoformat(self.clock());at=datetime.fromisoformat(facts['at'])
   if not now.tzinfo or not at.tzinfo:raise ValueError('timezone_required')
   checks=facts.get('checks',{});missing=[g for g in REQUIRED if checks.get(g) is not True]
   if facts.get('account_id')!=account or facts.get('plan_id')!=plan:missing.append('identity')
   if not facts.get('evidence') or not 0<=(now-at).total_seconds()<=60:missing.append('freshness')
   if missing:return stop('blocked',blockers=missing)
   current=Decimal(facts['current']);base=Decimal(b['amount'])
   if not current.is_finite() or current<0:raise ValueError('budget_invalid')
   with localcontext() as ctx:
    ctx.prec=40;target=min(current+base/3,base*Decimal('2.5')).quantize(Decimal('.01'),rounding=ROUND_FLOOR)
   if target<=current:return stop('blocked',blockers=['cap_or_decrease'])
   day=(now.astimezone(CHINA).date()-datetime.fromisoformat(b['live_on']).date()).days+1
   if day<1:return stop('blocked',blockers=['future_activation'])
   free=day<=30 and self.store.success_count(account,plan)==0
   if not free:
    # Actual identity/approval verification belongs to the trusted adapter.
    a=facts.get('approval') or {}
    valid=(a.get('identity_verified') is True and a.get('status')=='approved' and a.get('account_id')==account
      and a.get('plan_id')==plan and a.get('rule_version')==VERSION and a.get('request_id')==key
      and a.get('actor_id') and a.get('actor_id')==a.get('expected_actor_id') and a.get('evidence')
      and Decimal(str(a.get('before','NaN')))==current and Decimal(str(a.get('after','NaN')))==target)
    if valid:
     issued=datetime.fromisoformat(a['issued_at']);expiry=datetime.fromisoformat(a['expires_at'])
     valid=bool(issued.tzinfo and expiry.tzinfo and issued<=now<expiry<=issued+timedelta(hours=24))
    if not valid:return stop('approval_required',target=str(target))
   # Facts recorded before network. Exception here is definitely not sent.
   self.store.observe(account,plan,{'intent':key,'preflight':facts,'target':str(target),'baseline':b})
  except Exception as e:return stop('blocked',blockers=['preflight_invalid'],error_type=type(e).__name__)
  try:
   response=write(account,plan,str(target))
   rows=(response.get('data') or {}).get('results',[])
   matching=[r for r in rows if str(r.get('ad_id'))==plan]
   receipt={'target':str(target),'code':response.get('code'),'request_id':response.get('request_id')}
   if response.get('code')==0 and len(matching)==1 and matching[0].get('status')=='SUCCESS':
    actual=Decimal(str(readback(account,plan)));receipt['readback']=str(actual)
    status='success' if actual==target else 'uncertain'
   else:
    # Even an error is not retried; unknown server side effects remain blocked.
    status='uncertain'
  except BeforeSubmitBlocked:return stop('blocked',blockers=['submit_guard_changed'])
  except Exception as e:status='uncertain';receipt={'target':str(target),'error_type':type(e).__name__}
  self.store.finish(key,status,receipt)
  return dict(status=status,**receipt)

class OfficialBudgetWriter:
 """Exact official endpoint, one plan, no token refresh or automatic retry."""
 def __init__(self,read_client,allowed_plans):self.client=read_client;self.allowed=set(allowed_plans)
 def __call__(self,account,plan,target):
  import json,urllib.request
  from .read_transport import NoRedirect
  if account!=self.client.account or plan not in self.allowed:raise ValueError('write_scope')
  amount=Decimal(target)
  if not amount.is_finite() or amount<=0 or amount!=amount.quantize(Decimal('.01')):raise ValueError('invalid_amount')
  # Emit exact Decimal JSON numeric syntax; never binary floating-point money.
  body='{"advertiser_id":'+str(int(account))+',"update_budget_infos":[{"ad_id":'+str(int(plan))+',"budget":'+format(amount,'f')+'}]}'
  req=urllib.request.Request('https://api.oceanengine.com/open_api/v1.0/qianchuan/uni_promotion/ad/budget/update/',
   data=body.encode(),headers={'Access-Token':self.client.token_loader(),'Content-Type':'application/json'},method='POST')
  with urllib.request.build_opener(NoRedirect).open(req,timeout=30) as response:return json.load(response)
