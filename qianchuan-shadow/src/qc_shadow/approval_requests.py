"""Bound approval requests and append-only invalidation; uncertain sends are not replayed."""
import json,hashlib
from pathlib import Path
from datetime import datetime,timedelta
from .engine import VERSION

def save_once(path,value):
 with path.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2)
 path.chmod(0o600)
class ApprovalRequests:
 def __init__(self,home,reader):self.home=Path(home);self.home.mkdir(parents=True,exist_ok=True);self.reader=reader
 def identity(self,a,p,before,after,cycle):
  return hashlib.sha256(json.dumps([a,p,str(before),str(after),VERSION,cycle],separators=(',',':')).encode()).hexdigest()[:32]
 def request(self,a,p,before,after,cycle,now):
  key=self.identity(a,p,before,after,cycle);folder=self.home/key
  if folder.exists():return key
  folder.mkdir(mode=0o700)
  n=datetime.fromisoformat(now)
  record={'request_id':key,'account_id':a,'plan_id':p,'before':str(before),'after':str(after),'rule_version':VERSION,
   'issued_at':now,'expires_at':(n+timedelta(hours=24)).isoformat(),'expected_actor_id':self.reader.actor,'chat_id':self.reader.chat}
  save_once(folder/'request.json',record)
  # This durable marker precedes HTTP; a crash/timeout never causes automatic resend.
  save_once(folder/'sending.json',{'status':'uncertain','at':now})
  text=f"预算上调待确认｜账户{a}｜计划{p}\n日预算{before} → {after}元；规则{VERSION}；24小时内有效。\n请回复本条并@机器人，输入：同意预算 {key} 或 拒绝预算 {key}。\n执行前仍会重检；计划暂停关闭。\n<at user_id=\"{self.reader.actor}\">投手</at>"
  try:
   api=self.reader.api
   r=api._request('POST',api.BASE+'/im/v1/messages?receive_id_type=chat_id',{'receive_id':self.reader.chat,'msg_type':'text','content':json.dumps({'text':text},ensure_ascii=False),'uuid':key},{'Authorization':'Bearer '+api._tenant_token()})
   mid=r.get('data',{}).get('message_id')
   if r.get('code')!=0 or not mid:raise ValueError('approval_delivery_uncertain')
   save_once(folder/'sent.json',{'message_id':mid,'code':0,'at':now})
  except Exception as e:save_once(folder/'send_failure.json',{'status':'uncertain','type':type(e).__name__})
  return key
 def invalidate(self,key,reason,now):
  path=self.home/key/'invalidated.json'
  if not path.exists():save_once(path,{'reason':reason,'at':now})
 def poll(self,now):
  result=[]
  for folder in sorted(self.home.iterdir()):
   if not folder.is_dir() or not (folder/'request.json').exists() or not (folder/'sent.json').exists() or (folder/'invalidated.json').exists() or (folder/'consumed.json').exists():continue
   req=json.loads((folder/'request.json').read_text());req.update(json.loads((folder/'sent.json').read_text()))
   if datetime.fromisoformat(now)>=datetime.fromisoformat(req['expires_at']):self.invalidate(folder.name,'expired',now);continue
   replies=self.reader.matching_reply_ids(req)
   accepted=[]
   for mid in replies:
    value=self.reader.verify(req,mid,now)
    if value:accepted.append(value)
   # Conflicting/duplicate decisions never pick an arbitrary latest consent.
   if len(accepted)>1:self.invalidate(folder.name,'multiple_or_conflicting_replies',now);continue
   if accepted:
    value=accepted[0]
    if value['status']=='rejected':
     self.invalidate(folder.name,'rejected',now)
    else:result.append(value)
  return result
 def invalidate_plan(self,a,p,reason,now):
  for f in self.home.glob('*/request.json'):
   r=json.loads(f.read_text())
   if r['account_id']==a and r['plan_id']==p and datetime.fromisoformat(now)>=datetime.fromisoformat(r['issued_at']):self.invalidate(f.parent.name,reason,now)
 def notify_action(self,action,account):
  folder=self.home/('result-'+action['action_id'])
  if folder.exists():return 'already_recorded'
  folder.mkdir(mode=0o700);save_once(folder/'sending.json',{'at':datetime.now().astimezone().isoformat(),'status':'uncertain'})
  status=action['status'];label='已回读确认成功' if status=='success' else '结果不确定，请人工核验；不会自动重试'
  text=f"预算调整｜{label}\n账户{account}｜计划{action['plan_id']}\n目标预算：{action.get('target')}元；回读：{action.get('readback','未确认')}\n动作ID：{action['action_id']}\n计划暂停保持关闭。"
  try:
   api=self.reader.api;r=api._request('POST',api.BASE+'/im/v1/messages?receive_id_type=chat_id',{'receive_id':self.reader.chat,'msg_type':'text','content':json.dumps({'text':text},ensure_ascii=False),'uuid':hashlib.sha256(('result:'+action['action_id']).encode()).hexdigest()[:32]},{'Authorization':'Bearer '+api._tenant_token()})
   if r.get('code')!=0 or not r.get('data',{}).get('message_id'):raise ValueError('result_notice_uncertain')
   save_once(folder/'sent.json',{'code':0,'message_id':r['data']['message_id']});return 'sent'
  except Exception as e:save_once(folder/'failure.json',{'status':'uncertain','type':type(e).__name__});return 'uncertain'

 def invalidate_account(self,account,reason,now):
  for f in self.home.glob('*/request.json'):
   r=json.loads(f.read_text())
   if r['account_id']==account and datetime.fromisoformat(now)>=datetime.fromisoformat(r['issued_at']):self.invalidate(f.parent.name,reason,now)
 def invalidate_observed_balances(self,source):
  """A low sample remains invalidating even after recharge or a failed daily run."""
  import sqlite3
  from decimal import Decimal
  pending=[f for f in self.home.glob('*/request.json') if not (f.parent/'invalidated.json').exists() and not (f.parent/'consumed.json').exists()]
  if not pending:return True
  requests=[json.loads(f.read_text()) for f in pending];path=Path(source).resolve()/'data/qianchuan/monitor.db'
  if not path.is_file():return False
  try:
   with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3) as db:
    for account in {r['account_id'] for r in requests}:
     since=min(datetime.fromisoformat(r['issued_at']) for r in requests if r['account_id']==account)
     for stamp,raw in db.execute('SELECT stamp,payload FROM balances WHERE account=? AND stamp>=?',(account,str(since.date()))):
      value=json.loads(raw);at=value['fetched_at'];amount=Decimal(str(value['amounts_yuan']['account_valid']))
      if not amount.is_finite() or value['advertiser_id']!=account:return False
      if datetime.fromisoformat(at)>=since and amount<=500:self.invalidate_account(account,'historical_low_balance_sample',at)
   return True
  except (ValueError,KeyError,TypeError,sqlite3.Error):return False
