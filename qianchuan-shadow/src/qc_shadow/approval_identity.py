"""Exact human-message approval, verified again through Feishu's read API."""
import json,hashlib,re,sqlite3
from pathlib import Path
from datetime import datetime,timedelta,timezone
from urllib.parse import urlencode,quote

def aware(value):
 d=datetime.fromisoformat(value)
 if not d.tzinfo:raise ValueError('timezone_required')
 return d

def verify_reply(request,message,now,member_verified):
 try:
  now=aware(now);issued=aware(request['issued_at']);expiry=aware(request['expires_at'])
  created=datetime.fromtimestamp(int(message['create_time'])/1000,timezone.utc);sender=message['sender']
  if not (member_verified is True and issued<=created<=now<expiry<=issued+timedelta(hours=24)
   and sender.get('id')==request['expected_actor_id'] and sender.get('id_type')=='open_id' and sender.get('sender_type')=='user'
   and message.get('chat_id')==request['chat_id'] and message.get('parent_id')==request['message_id']
   and message.get('deleted') is False and message.get('updated') is False and message.get('msg_type')=='text'):return None
  text=json.loads(message['body']['content']).get('text','').strip()
  # Only structured mention placeholders are removed; arbitrary prose is never parsed as consent.
  for mention in message.get('mentions',[]):
   key=mention.get('key','')
   if re.fullmatch(r'@_user_\d+',key):text=text.replace(key,'').strip()
  if text not in ('同意预算 '+request['request_id'],'拒绝预算 '+request['request_id']):return None
  result={k:request[k] for k in ('request_id','account_id','plan_id','before','after','rule_version','issued_at','expires_at','expected_actor_id')}
  result.update(status='approved' if text.startswith('同意') else 'rejected',actor_id=sender['id'],identity_verified=True,
    evidence='feishu:message:'+message['message_id']+':sha256:'+hashlib.sha256(json.dumps(message,sort_keys=True).encode()).hexdigest())
  return result
 except (KeyError,ValueError,TypeError,OverflowError):return None

class FeishuApprovalReader:
 def __init__(self,source):
  import sys
  sys.path.insert(0,str(source))
  from agent.settings import load_env
  from agent.notify import FeishuAppNotifier,feishu_allowed_chat_ids
  self.source=Path(source);env=load_env(self.source/'config/.env');cfg=json.loads((self.source/'config/rules.json').read_text())
  actors=cfg.get('notify',{}).get('roles',{}).get('投手',[])
  if len(actors)!=1 or not re.fullmatch(r'ou_[a-zA-Z0-9]+',actors[0]):raise ValueError('single_explicit_approver_required')
  self.actor=actors[0];self.allowed=set(feishu_allowed_chat_ids(env));self.chat=env.get('FEISHU_ALERT_CHAT_ID') or env.get('FEISHU_CHAT_ID')
  if self.chat not in self.allowed:raise ValueError('approval_group_not_allowed')
  self.app_id=env['FEISHU_APP_ID'];self.api=FeishuAppNotifier(self.app_id,env['FEISHU_APP_SECRET'],self.chat)
 def get(self,path):
  return self.api._request('GET',self.api.BASE+path,headers={'Authorization':'Bearer '+self.api._tenant_token()})
 def member(self):
  page=None;seen=set()
  for _ in range(100):
   params={'member_id_type':'open_id','page_size':100}
   if page:params['page_token']=page
   r=self.get('/im/v1/chats/'+quote(self.chat,safe='')+'/members?'+urlencode(params));d=r.get('data',{})
   if r.get('code')!=0:raise ValueError('membership_api_failed')
   if any(x.get('member_id_type')=='open_id' and x.get('member_id')==self.actor for x in d.get('items',[])):return True
   if d.get('has_more') is False:return False
   page=d.get('page_token')
   if not page or page in seen:raise ValueError('membership_pagination')
   seen.add(page)
  raise ValueError('membership_page_limit')
 def verify(self,request,message_id,now):
  if request.get('expected_actor_id')!=self.actor or request.get('chat_id')!=self.chat:raise ValueError('approval_scope_changed')
  if not re.fullmatch(r'om_[a-zA-Z0-9]+',message_id):raise ValueError('message_id_invalid')
  response=self.get('/im/v1/messages/'+message_id+'?user_id_type=open_id')
  items=response.get('data',{}).get('items',[])
  if response.get('code')!=0 or len(items)!=1 or items[0].get('message_id')!=message_id:return None
  return verify_reply(request,items[0],now,True)  # authenticated API sender + chat, not an event assertion
 def matching_reply_ids(self,request):
  # Inbox is only a discovery index. The API, not stored sender/text, proves identity.
  path=self.source/'data/listener.db';ids=[]
  with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
   for mid,raw in db.execute('SELECT message_id,payload FROM message_context'):
    c=json.loads(raw)
    if c.get('parent_id')==request['message_id'] and c.get('chat_id')==self.chat:ids.append(mid)
  return ids
