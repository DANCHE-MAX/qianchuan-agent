"""User-authorized collection boundary; retained history is never deleted."""
from datetime import date
ACCOUNT='9000000000000099'
START=date(2026,10,22)
VERSION='post-20261021-v1'
def floor_day(account,day):return max(START,day) if str(account)==ACCOUNT else day
def scoped_changes(old,new,account):
 from .daily_refresh import changes
 if str(account)==ACCOUNT:
  old=[r for r in old if r['day']>=str(START)]
  new=[r for r in new if r['day']>=str(START)]
 return changes(old,new)

# Explicit user decision, 2026-11-04: completeness/cumulative evidence starts here.
def decision_start(account,first=None):
 if str(account)==ACCOUNT:return max(str(START),first) if first else str(START)
 return first

def scoped_lifecycle(account,rows,first,yesterday,refund,live_on):
 from .lifecycle_replay import replay
 if str(account)!=ACCOUNT:return replay(rows,first,yesterday,refund,live_on)
 return replay(rows,first,yesterday,refund,live_on,observation_start=str(START))
