"""Exact business acknowledgements of refund-basis differences, never a money adjustment."""
from decimal import Decimal
from pathlib import Path
import json
CONFLICT='与原日报采集结果不一致，待核验'

def records(directory):
 for root in (Path(directory).resolve(),*Path(directory).resolve().parents):
  path=root/'data/refund_confirmations/confirmed.json'
  if path.exists():
   value=json.loads(path.read_text())
   if value.get('version')!=1:raise ValueError('Invalid refund confirmation version')
   return value['records']
 return []

def number(v):
 if v is None:raise ValueError('Missing amount')
 n=Decimal(str(v))
 if not n.is_finite() or n<0:raise ValueError('Invalid amount')
 return n

def differences(day,report):
 from .two_day_daily import grouped_plans
 if not report or str(day.get('account'))!=str(report['account']) or day['day']!=report['date'] or day.get('conflicts'):raise ValueError('Scope conflict')
 plans=grouped_plans(day);base={str(r['plan_id']):r for r in report['rows']}
 if len(base)!=len(report['rows']):raise ValueError('Duplicate plan')
 result=[]
 for pid in set(plans)|set(base):
  p=plans.get(pid);b=base.get(pid)
  if p is None:
   if any(b.get(k) is not None and number(b[k])!=0 for k in ('gmv','spend')):raise ValueError('Unmatched plan')
   continue
  if b is None:raise ValueError('Unmatched plan')
  for metric in ('gmv','spend'):
   old,new=number(b.get(metric)),number(p.get(metric))
   if old.quantize(Decimal('.01'))!=new.quantize(Decimal('.01')):result.append((pid,metric,old,new))
 return result

def covered(day,report,confirmations):
 try:
  diffs=differences(day,report);ids=[]
  if not diffs:return []
  for pid,metric,old,new in diffs:
   matches=[r for r in confirmations if r.get('status')=='business_confirmed' and r.get('kind')=='refund_basis_difference' and str(r.get('account'))==str(report['account']) and r.get('date')==day['day'] and str(r.get('plan_id'))==pid and r.get('metric')==metric=='gmv' and number(r.get('original'))==old and number(r.get('comparison'))==new and number(r.get('amount'))==abs(new-old)]
   if len(matches)!=1 or not matches[0].get('id'):return []
   ids.append(matches[0]['id'])
  return sorted(ids)
 except (ValueError,KeyError,ArithmeticError,TypeError):return []

def annotate(day,report,confirmations):
 ids=covered(day,report,confirmations)
 if not ids:return
 day['complete']=False # Never present the inconsistent prior-day profit pie as reconciled.
 day['confirmed_refund_differences']=ids
 day['warnings']=[w for w in day.get('warnings',[]) if w!=CONFLICT]
 amount=sum(abs(new-old) for _,_,old,new in differences(day,report))
 note=f'业务确认退款口径差异{amount:.2f}元；保留原值，不重复扣减利润；该日利润对比待核验'
 if note not in day['warnings']:day['warnings'].append(note)
