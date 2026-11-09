"""Independent full-day quality predicates; not permission to adjust or pause."""
from datetime import timedelta
from decimal import Decimal,localcontext
from .contracts import Daily
from .history import at

def assess_daily(rows,as_of,refund,estimator=None):
    with localcontext() as context:
        context.prec=40
        return _assess(rows,as_of,refund,estimator)

def _assess(rows,as_of,refund,estimator=None):
    end=at(as_of).date()-timedelta(days=1);values={};duplicate=False
    for raw in rows:
        r=Daily.model_validate({k:raw.get(k) for k in ('day','spend','gmv','orders','complete','evidence')})
        if r.day in values:duplicate=True
        values[r.day]=r
    rate=None
    if refund.get('verified') and refund.get('band') and refund.get('model_version'):
        try:
            number=Decimal(str(refund.get('rate')))
            if number.is_finite() and 0<=number<=1:rate=number
        except Exception:pass
    profits={}
    for day,r in values.items():
        if day<=end and r.complete and r.evidence and r.spend is not None and r.gmv is not None and rate is not None:
            profits[day]=(estimator(r.gmv,r.spend) if estimator else r.gmv*(1-rate)*Decimal('.5566')-r.spend)
    def window(n):return [end-timedelta(days=i) for i in reversed(range(n))]
    def ready(n):return not duplicate and all(d in profits and values[d].orders is not None for d in window(n))
    grow='unknown';stable='unknown'
    if ready(2):
        a,b=window(2);grow='met' if profits[a]>0 and profits[b]>0 and values[a].orders>=5 and values[b].orders>=values[a].orders*2 else 'not_met'
    if ready(7):stable='met' if all(profits[d]>0 for d in window(7)) else 'not_met'
    loss='unknown' if not ready(3) else 'met' if all(profits[d]<0 for d in window(3)) else 'not_met'
    last=values.get(end);known=last and last.complete and last.evidence and last.spend is not None and last.gmv is not None
    return {'as_of_day':str(end),'Q-GROW':grow,'Q-STABLE':stable,'three_loss_days':loss,
        'last_day_profitable':None if not ready(1) else profits[end]>0,
        'last_day':None if not known else {'spend':str(last.spend),'gmv':str(last.gmv),'paid_orders':last.orders,
          'payment_roi':str(last.gmv/last.spend) if last.spend else None,'estimated_profit':str(profits[end]) if end in profits else None},
        'missing_last7_days':[str(d) for d in window(7) if d not in values or not values[d].complete],
        'refund_verified':rate is not None,'model_version':refund.get('model_version'),
        'execution_allowed':False,'note':'仅完整日质量分项；不代替生命周期、保护、预算、资金及审批门禁；三日亏损不能单独认定应暂停。'}
