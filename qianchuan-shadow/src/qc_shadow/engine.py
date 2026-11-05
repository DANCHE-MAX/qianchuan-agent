"""Pure shadow rules v1.0. No clocks, files, network or production execution."""
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR, localcontext
from zoneinfo import ZoneInfo
from .contracts import Evaluation
VERSION='business-v1.0'
D=Decimal
CHINA=ZoneInfo('Asia/Shanghai')

def evaluate(x: Evaluation):
    with localcontext() as ctx:
        ctx.prec=40
        return _evaluate(x)

def _evaluate(x):
    now=x.as_of.astimezone(CHINA);yesterday=now.date()-timedelta(days=1)
    out=dict(rule_version=VERSION,mode='simulation' if x.simulation else 'shadow',execution_allowed=False,
             action='observe',rules=[],blockers=[],metrics={},target_budget=None,state_proposals=[],invalidated_requests=[],
             reminder='not_scheduled_pending_weekly_anchor_policy')
    def block(code):
        if code not in out['blockers']:out['blockers'].append(code)
    def finish(action=None):
        if action:out['action']=action
        if out['action']=='defer':out['state_proposals']=[]
        if x.evaluation_kind=='intraday':
            out['state_proposals']=[]
            if out['action']=='pause':
                block('daily_pause_rule_blocks_intraday_increase');out['action']='observe'
        return out
    st=x.state;b=x.budget
    low=x.balance.available is not None and x.balance.available<=500
    invalidating=low or b.manual_changed or x.platform_active is False
    if invalidating and st.pending_request_id:out['invalidated_requests'].append(st.pending_request_id)
    if x.scope!='chuanfang_goods':block('scope_not_admitted');return finish('defer')
    if x.evaluation_kind=='daily' and now.hour<10:block('daily_evaluation_not_due');return finish('defer')
    if x.evaluation_kind=='intraday' and (not x.daily_assessment_evidence or x.daily_assessed_through!=yesterday):
        block('prior_daily_assessment_required');return finish('defer')
    a=x.approval
    if a and st.pending_request_id==a.request_id and (a.status=='invalidated' or now>=a.expires_at or b.manual_changed or low or x.platform_active is False):
        if a.request_id not in out['invalidated_requests']:out['invalidated_requests'].append(a.request_id)
    # In live shadow mode a supplied date cannot silently start the production clock.
    rule_day=(now.date()-x.rule_live_on).days+1 if x.simulation and x.rule_live_on else None
    out['metrics']['rule_day']=rule_day
    if rule_day is None:block('rule_clock_not_started')
    elif rule_day<1:block('rule_clock_in_future');return finish('defer')
    if x.first_enabled_on is None or not x.first_enabled_evidence:
        block('first_enabled_unknown');return finish('defer')
    age=(yesterday-x.first_enabled_on).days+1
    out['metrics']['completed_test_days']=age
    if age<1:block('no_complete_test_day');return finish('defer')
    rows={r.day:r for r in x.daily}
    if len(rows)!=len(x.daily):block('duplicate_daily_fact');return finish('defer')
    expected=[x.first_enabled_on+timedelta(days=i) for i in range(age)]
    if any(day not in rows or not rows[day].complete or not rows[day].evidence or
           rows[day].spend is None or rows[day].orders is None for day in expected):
        block('daily_spend_orders_incomplete');return finish('defer')
    days=[rows[d] for d in expected];spend=sum((r.spend for r in days),D(0));orders=sum(r.orders for r in days)
    out['metrics'].update(window_start=str(expected[0]),window_end=str(expected[-1]),cumulative_spend=str(spend),paid_orders=orders)
    model_ok=x.refund_rate is not None and x.refund_verified and bool(x.refund_band and x.model_version)
    profit={r.day:r.gmv*(1-x.refund_rate)*D('.5566')-r.spend for r in days if model_ok and r.gmv is not None}
    total=sum(profit.values(),D(0)) if len(profit)==len(days) else None
    gmv=sum((r.gmv for r in days),D(0)) if all(r.gmv is not None for r in days) else None
    out['metrics'].update(estimated_profit=str(total) if total is not None else None,
        payment_roi=str(gmv/spend) if gmv is not None and spend else None,model_version=x.model_version,
        refund_rate=str(x.refund_rate) if x.refund_rate is not None else None,
        daily_estimated_profit={str(k):str(v) for k,v in profit.items()})
    def recent(n,predicate):
        return age>=n and all((yesterday-timedelta(days=i)) in profit and predicate(profit[yesterday-timedelta(days=i)]) for i in range(n))
    if st.protected_until:
        grant=st.protection_granted_at.astimezone(CHINA) if st.protection_granted_at else None
        granted_day=(grant.date()-x.rule_live_on).days+1 if grant and x.rule_live_on else None
        if not (grant and st.protection_evidence and grant<=now and grant.date()>=x.first_enabled_on
                and st.protected_until==x.first_enabled_on+timedelta(days=29) and granted_day and 1<=granted_day<=30):
            block('protection_grant_unverified');return finish('defer')
    protected=bool(st.protected_until and yesterday<st.protected_until)
    recovery=bool(st.recovery_until and st.recovery_evidence and now.date()<=st.recovery_until)
    if protected:block('observation_protection')
    if recovery:block('recovery_observation')
    def pause(code):
        out['rules'].append(code)
        if recovery and code in st.exempt_rules:
            block('recovery_rule_exemption');return False
        out['state_proposals'].append({'event':'would_pause','rule':code,'window_end':str(yesterday)})
        return True
    node={'initial':3,'to7':7,'to15':15,'to30':30}.get(st.stage)
    if st.stage=='initial' and 3 in st.evaluated_nodes:node=7 if 7 not in st.evaluated_nodes else None
    if node in st.evaluated_nodes:node=None
    if node and age>node:
        block('late_node_policy_unresolved');return finish('defer')
    if node and age==node:
        out['state_proposals'].append({'event':'node_evaluated','node':node,'window_end':str(yesterday)})
        if node==3 and orders==0:
            if spend>=30:
                if pause('T03-A'):return finish('pause')
            elif rule_day is None:return finish('defer')
            elif rule_day<=30:out['rules'].append('T03-B');out['state_proposals'].append({'stage':'to7'})
            elif pause('T03-C'):return finish('pause')
        elif node==7 and orders==0:
            if pause('T07-A'):return finish('pause')
        elif total is None:block('refund_or_gmv_unverified');return finish('defer')
        elif total>0 and orders>0:
            out['rules'].append('PASS');out['state_proposals'].append({'stage':'passed'})
        elif total<0:
            if node==3:out['rules'].append('T03-D');out['state_proposals'].append({'stage':'to7'})
            elif node==7:
                if recent(3,lambda p:p>0):out['rules'].append('T07-B');out['state_proposals'].append({'stage':'to15'})
                elif pause('T07-C'):return finish('pause')
            elif node==15:
                if rule_day is None:return finish('defer')
                if rule_day<=30:
                    out['rules'].append('T15-A');out['state_proposals'].append({'stage':'to30','protected_until':str(x.first_enabled_on+timedelta(days=29)),'protection_granted_at':now.isoformat(),'evidence_required':'accepted_state_event_id'})
                    block('observation_protection')
                elif not protected and pause('T15-B'):return finish('pause')
            elif node==30 and pause('T30'):return finish('pause')
    if (st.stage=='passed' or st.qualified_before) and recent(3,lambda p:p<0) and not protected:
        if rule_day is None:return finish('defer')
        if rule_day>30:
            if pause('QUALITY-LOSS-3D'):return finish('pause')
        else:out['rules'].append('QUALITY-DOWNGRADE')
    if not model_ok:block('refund_unverified')
    if total is None or total==0:block('cumulative_unknown_or_break_even')
    if not recent(1,lambda p:p>0):block('last_day_not_profitable')
    grow=recent(2,lambda p:p>0) and days[-2].orders>=5 and days[-1].orders>=2*days[-2].orders
    stable=recent(7,lambda p:p>0)
    if grow:out['rules'].append('Q-GROW')
    if stable:out['rules'].append('Q-STABLE')
    if not (grow or stable):block('quality_not_met')
    if not x.balance.verified or x.balance.available is None:block('balance_unverified')
    elif low:block('low_balance')
    if not x.platform_verified or x.platform_active is not True:block('platform_not_active_verified')
    if b.baseline is None or not b.baseline_evidence or b.current is None or b.mode!='day' or not b.verified:block('budget_unverified')
    if not b.precision_verified or b.quantum is None:block('budget_precision_unknown')
    if b.manual_changed:block('manual_budget_changed')
    if b.last_changed_at and yesterday<=b.last_changed_at.astimezone(CHINA).date():block('budget_observation_day')
    if st.rejected_at and yesterday<=st.rejected_at.astimezone(CHINA).date():block('rejection_observation_day')
    if not x.intraday_verified or not (x.platform_budget_limited or
            (b.current is not None and x.today_spend is not None and x.today_spend>=b.current*D('.9'))):block('budget_constraint_unverified')
    if out['blockers']:return finish()
    target=min(b.current+b.baseline/3,b.baseline*D('2.5'))
    # Platform quantum must be independently verified; floor cannot cross hard cap.
    target=(target/b.quantum).to_integral_value(rounding=ROUND_FLOOR)*b.quantum
    if target<=b.current:block('budget_cap_or_step');return finish()
    out['target_budget']=str(target.normalize()) if target!=target.to_integral_value() else str(int(target))
    free=(rule_day<=30 and st.assistant_increases==0) or (x.waiver_approved and bool(x.waiver_evidence))
    if free:return finish('increase')
    a=x.approval
    valid=bool(a and a.status=='approved' and a.identity_verified and a.evidence and a.actor_id==a.expected_actor_id
        and a.account_id==x.account_id and a.plan_id==x.plan_id and a.rule_version==VERSION and a.before==b.current and a.after==target
        and a.issued_at<=now<a.expires_at and a.expires_at<=a.issued_at+timedelta(hours=24)
        and a.request_id==st.pending_request_id and not invalidating)
    if valid:return finish('increase')
    return finish('request_approval')
