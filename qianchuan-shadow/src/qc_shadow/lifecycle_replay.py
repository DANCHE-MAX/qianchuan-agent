"""Replay expired nodes without actions; retain current-node and protection vetoes."""
from datetime import date,timedelta
from decimal import Decimal
from .history import amount

def replay(rows,first,yesterday,refund,live_on,observation_start=None):
 if first is None and observation_start:
  return {"stage":"observation_window_only","events":[],"budget_vetoes":[],"execution_allowed":False,"observation_start":observation_start,"actual_first_enabled_unknown":True}
 first=date.fromisoformat(first);live=date.fromisoformat(live_on);age=(yesterday-first).days+1
 byday={r['day']:r for r in rows};events=[];veto=[];stage='initial'
 for node,expected,next_stage in [(3,'initial','to7'),(7,'to7','to15'),(15,'to15','to30'),(30,'to30','passed')]:
  if age<node or stage!=expected:continue
  end=first+timedelta(days=node-1)
  if observation_start and str(end)<observation_start:
   events.append({'node':node,'window_end':str(end),'rule':'outside_authorized_window','production_action':False})
   stage=next_stage
   continue
  window=[byday[str(first+timedelta(days=i))] for i in range(node) if not observation_start or str(first+timedelta(days=i))>=observation_start]
  profits=[amount(r['gmv'])*(1-amount(refund))*Decimal('.5566')-amount(r['spend']) for r in window]
  profit=sum(profits,Decimal(0));orders=sum(int(r['orders']) for r in window);spend=sum((amount(r['spend']) for r in window),Decimal(0))
  current=age==node;action='observe';rule=''
  if node==3 and orders==0:
   if spend>=30:action='would_pause';rule='T03-A'
   elif current and (yesterday+timedelta(days=1)-live).days+1>30:action='would_pause';rule='T03-C'
   else:stage='to7';rule='T03-B'
  elif node==7 and orders==0:action='would_pause';rule='T07-A'
  elif profit>0 and orders>0:stage='passed';rule='PASS'
  elif profit<0:
   if node==3:stage='to7';rule='T03-D'
   elif node==7:
    if all(p>0 for p in profits[-3:]):stage='to15';rule='T07-B'
    else:action='would_pause';rule='T07-C'
   elif node==15:
    stage='to30';rule='T15'
    # No backdated grants. A node arising after activation needs accepted protection
    # or blocks increases conservatively until its 30-day test window has closed.
    due=first+timedelta(days=node)
    if due>=live and age<30:veto.append('current_or_pending_protection')
   else:action='would_pause';rule='T30'
  else:rule='break_even_observe'
  events.append({'node':node,'window_end':str(first+timedelta(days=node-1)),'rule':rule,'action':action,'expired':not current,'production_action':False})
  if action=='would_pause':
   if current:veto.append(rule)
   # The user explicitly chose no retroactive execution. Do not invent a pass or resume.
   if current:stage='current_pause_rule';break
   # Continued real-world operation still has the next observation node; this
   # records no pass, pause or resume and does not erase a current-node veto.
   stage=next_stage if node<30 else 'historical_pause_not_executed'
  if profit==0 and orders>0:break
 return {'stage':stage,'events':events,'budget_vetoes':veto,'execution_allowed':False}
