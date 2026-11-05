"""Native worker orchestration: fresh preflight, exact approvals, budget-only writes."""
import json,hashlib
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_FLOOR,localcontext
from zoneinfo import ZoneInfo
from .execution_store import ExecutionStore
from .creator_cost import budget_cost_ready
from .preflight import build_preflight
from .live_preflight import LiveReader
from .approval_identity import FeishuApprovalReader
from .approval_requests import ApprovalRequests,save_once
from .budget_execution import BudgetExecutor,OfficialBudgetWriter,BeforeSubmitBlocked
CHINA=ZoneInfo('Asia/Shanghai')
def clock():return datetime.now(CHINA).isoformat()
def run_cycle(home,source,snapshots,reader_factory=LiveReader,approval_factory=FeishuApprovalReader):
 home=Path(home);activation_file=home/'activation.json'
 if not activation_file.exists():return {'enabled':False,'reason':'not_activated','actions':[]}
 activation=json.loads(activation_file.read_text())
 if activation.get('authorized') is not True:return {'enabled':False,'reason':'not_authorized','actions':[]}
 store=ExecutionStore(home/'execution.db');now=datetime.now(CHINA);result={'enabled':True,'plan_pause_enabled':False,'actions':[],'blocked':[]}
 approval_history_ok=ApprovalRequests(home/'approvals',None).invalidate_observed_balances(source)
 live_reader=None;approvals=None;verified=[]
 for snapshot in snapshots:
  if snapshot['account_id']!=activation['account_id']:continue
  invalidation=ApprovalRequests(home/'approvals',None)
  balance=snapshot.get('balance') or {}
  amount=balance.get('amounts_yuan',{}).get('account_valid')
  if amount is not None and Decimal(str(amount))<=500 and balance.get('fetched_at'):
   invalidation.invalidate_account(snapshot['account_id'],'observed_low_balance',balance['fetched_at'])
  for p in snapshot['plans']:
   if p['plan_id'] not in activation['plans']:continue
   pid=p['plan_id'];a=snapshot['account_id'];baseline=store.baseline(a,pid)
   readiness=json.loads((home/(a+'-readiness.json')).read_text());fact=next(x for x in readiness['plans'] if x['plan_id']==pid)
   status=p.get('status') or {}
   if status.get('opt_status') and status['opt_status']!='ENABLE' and status.get('fetched_at'):
    invalidation.invalidate_plan(a,pid,'observed_plan_inactive',status['fetched_at'])
   for f in (home/'approvals').glob('*/request.json'):
    req=json.loads(f.read_text());budget=p.get('budget') or {}
    if req['account_id']==a and req['plan_id']==pid and budget.get('amount') is not None and Decimal(str(budget['amount']))!=Decimal(req['before']) and budget.get('fetched_at'):
     invalidation.invalidate_plan(a,pid,'observed_budget_change',budget['fetched_at'])
   static=[]
   if not budget_cost_ready(a):static.append("creator_commission_cost_unreconciled")
   if not baseline:static.append('fixed_B_missing')
   elif baseline['live_on']!=activation.get('live_on'):static.append('activation_clock_mismatch')
   if now.hour<10:static.append('daily_assessment_not_due')
   if fact['missing_daily_dates'] is None or fact['missing_daily_dates']:static.append('full_history_incomplete')
   if p.get('refund',{}).get('verified') is not True:static.append('refund_unverified')
   if snapshot.get('pending_execution_revisions'):static.append('revision_review_pending')
   quality=fact['quality']
   if not (quality.get('Q-GROW')=='met' or quality.get('Q-STABLE')=='met'):static.append('quality_not_met')
   if static:
    result['blocked'].append({'plan_id':pid,'checks':static})
    continue
   try:
    if live_reader is None:live_reader=reader_factory(source,home/'live')
    captured=live_reader.capture(pid)
    current_facts=build_preflight(snapshot,captured,json.loads(activation_file.read_text()),clock())
    if not all(current_facts['checks'].values()):
     if not all(current_facts['checks'][k] for k in ('budget_unchanged','balance_above_500','platform_active')):
      if approvals is None:approvals=ApprovalRequests(home/'approvals',approval_factory(source))
      if not current_facts['checks']['balance_above_500']:approvals.invalidate_account(a,'low_balance',clock())
      else:approvals.invalidate_plan(a,pid,'budget_or_platform_changed',clock())
     result['blocked'].append({'plan_id':pid,'checks':current_facts['blocking_checks']});continue
    with localcontext() as ctx:
     ctx.prec=40;before=Decimal(current_facts['current']);B=Decimal(baseline['amount']);target=min(before+B/3,B*Decimal('2.5')).quantize(Decimal('.01'),rounding=ROUND_FLOOR)
    if target<=before:result['blocked'].append({'plan_id':pid,'checks':['cap']});continue
    free=(now.date()-datetime.fromisoformat(baseline['live_on']).date()).days<30 and store.success_count(a,pid)==0
    if approvals is None:
     approvals=ApprovalRequests(home/'approvals',approval_factory(source));verified=approvals.poll(clock())
    # A rejection requires a complete natural day, even for a different request ID.
    rejected=[]
    for f in (home/'approvals').glob('*/invalidated.json'):
     invalid=json.loads(f.read_text());request=json.loads((f.parent/'request.json').read_text())
     if request['account_id']==a and request['plan_id']==pid and invalid['reason']=='rejected':rejected.append(datetime.fromisoformat(invalid['at']).astimezone(CHINA).date())
    if rejected and now.date()-timedelta(days=1)<=max(rejected):result['blocked'].append({'plan_id':pid,'checks':['rejection_observation']});continue
    key=hashlib.sha256(json.dumps([a,pid,str(before),str(target),str(now.date()),'first'],separators=(',',':')).encode()).hexdigest()
    approval=None
    if not free:
     if not approval_history_ok:result['blocked'].append({'plan_id':pid,'checks':['approval_balance_history_unverified']});continue
     key=approvals.request(a,pid,str(before),str(target),str(now.date()),clock())
     approval=next((r for r in verified if r['request_id']==key),None)
     if not approval:result['blocked'].append({'plan_id':pid,'checks':['approval_required'],'request_id':key});continue
    def preflight():
     # Re-read all platform facts immediately before write, never reuse the discovery check.
     captured=live_reader.capture(pid)
     fresh=build_preflight(snapshot,captured,json.loads(activation_file.read_text()),clock())
     if not fresh['checks']['balance_above_500']:approvals.invalidate_account(a,'low_balance',clock())
     elif not fresh['checks']['budget_unchanged'] or not fresh['checks']['platform_active']:approvals.invalidate_plan(a,pid,'budget_or_platform_changed',clock())
     if fresh['current']!=str(before):fresh['checks']['budget_unchanged']=False
     if approval:
      latest=approvals.poll(clock());fresh['approval']=next((r for r in latest if r['request_id']==key),None)
      if not fresh['approval']:fresh['checks']['authorized']=False
     return fresh
    official=OfficialBudgetWriter(live_reader.client,activation['plans'])
    def write(account,plan,target):
     latest_activation=json.loads(activation_file.read_text())
     if latest_activation!=activation or latest_activation.get('authorized') is not True:raise BeforeSubmitBlocked('activation_changed_before_submit')
     if approval:save_once(home/'approvals'/key/'consumed.json',{'at':clock(),'request_id':key})
     return official(account,plan,target)
    action=BudgetExecutor(store,{a:set(activation['plans'])},clock).execute(key,a,pid,preflight,write,live_reader.readback)
    action.update(plan_id=pid,action_id=key);result['actions'].append(action)
    if action['status'] in ('success','uncertain'):action['notification']=approvals.notify_action(action,a)
    if approval and action['status']!='success':approvals.invalidate(key,'execution_recheck_or_failure',clock())
   except Exception as e:result['blocked'].append({'plan_id':pid,'checks':['preflight_or_approval_unavailable'],'error_type':type(e).__name__,'api_code':getattr(e,'api_code',getattr(e,'code',None))})
 path=home/'budget-cycle-status.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(dict(result,at=clock()),ensure_ascii=False,indent=2));tmp.chmod(0o600);tmp.replace(path)
 return result
