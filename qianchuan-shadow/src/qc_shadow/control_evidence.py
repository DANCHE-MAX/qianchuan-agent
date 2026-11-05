"""Persistent readiness facts, distinct from accepted lifecycle and fixed B.

Missing historical daily rows remain unknown. No production action is imported.
"""
import json
from pathlib import Path
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from .execution_store import ExecutionStore
from .daily_rules import assess_daily
from .collection_scope import decision_start,scoped_lifecycle,ACCOUNT
from .plan_audit import valid_day

def record_snapshot(home,snapshot):
 home=Path(home);home.mkdir(parents=True,exist_ok=True);store=ExecutionStore(home/'execution.db')
 today=datetime.fromisoformat(snapshot['as_of']).astimezone(ZoneInfo('Asia/Shanghai')).date();yesterday=today-timedelta(days=1)
 result={'account_id':snapshot['account_id'],'as_of':snapshot['as_of'],'execution_allowed':False,'plan_pause_enabled':False,'plans':[]}
 for p in snapshot['plans']:
  if p['scope']!='chuanfang_goods':continue
  pid=p['plan_id'];refund=p.get('refund') or {};first=p.get('first_enabled_on');rows=p.get('daily',[])
  complete={r['day'] for r in rows if valid_day(r) and sum(x['day']==r['day'] for x in rows)==1}
  window_start=decision_start(snapshot['account_id'],first)
  missing=None;nodes=[]
  if window_start:
   start=date.fromisoformat(window_start);missing=[str(start+timedelta(days=i)) for i in range(max(0,(yesterday-start).days+1)) if str(start+timedelta(days=i)) not in complete]
   for n in ((3,7,15,30) if first else ()):
    start=date.fromisoformat(first)
    end=start+timedelta(days=n-1)
    covered=end<=yesterday and all(str(start+timedelta(days=i)) in complete for i in range(n))
    nodes.append({'node':n,'window_end':str(end),'data_complete':covered,'status':'ready_for_read_only_replay' if covered and refund.get('verified') else 'waiting_for_evidence','expired_actions_enabled':False})
  baseline=store.baseline(snapshot['account_id'],pid);blockers=[]
  if not first and snapshot['account_id']!=ACCOUNT:blockers.append('first_enabled_unknown')
  if missing is None or missing:blockers.append('full_history_incomplete')
  if not refund.get('verified'):blockers.append('refund_mapping_unverified')
  if snapshot.get('pending_execution_revisions'):blockers.append('daily_revision_review_pending')
  if not baseline:blockers.append('formal_B_awaits_execution_go_live')
  activation_path=home/'activation.json'
  activation=json.loads(activation_path.read_text()) if activation_path.exists() else {}
  if not activation.get('authorized'):blockers.append('production_control_not_activated')
  if missing is None or missing:blockers.append('lifecycle_history_incomplete')
  lifecycle={'stage':'unverified','events':[]}
  if window_start and missing==[] and refund.get('verified') and activation.get('live_on'):
   from .lifecycle_replay import replay
   lifecycle=scoped_lifecycle(snapshot['account_id'],rows,first,yesterday,refund['rate'],activation['live_on'])
   blockers+=lifecycle['budget_vetoes']
  blockers.append('fresh_execution_precheck_required')
  fact={'plan_id':pid,'as_of':snapshot['as_of'],'first_enabled_on':first,
   'first_enabled_evidence':p.get('first_enabled_evidence'),'completeness_start':window_start,'missing_daily_dates':missing,
   'historical_policy':'replay_only_current_decisions','lifecycle_stage':lifecycle['stage'],'lifecycle_replay':lifecycle,'nodes':nodes,
   'refund':refund,'budget_candidate':p.get('budget'),'formal_B':baseline,
   'assistant_success_count':store.success_count(snapshot['account_id'],pid),
   'quality':assess_daily(rows,snapshot['as_of'],refund),'blockers':blockers,
   'execution_allowed':False,'plan_pause_enabled':False}
  fact['evidence_event']=store.observe(snapshot['account_id'],pid,fact);result['plans'].append(fact)
 target=home/(snapshot['account_id']+'-readiness.json');tmp=target.with_suffix('.tmp')
 tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2));tmp.chmod(0o600);tmp.replace(target)
 return result
