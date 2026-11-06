"""Durable multi-part Feishu delivery; ambiguous sends stop instead of duplicating."""
import hashlib,json,time
from pathlib import Path
import requests
from .profit_daily import save

class AmbiguousDelivery(RuntimeError):pass

def upload(api,kind,path):
    endpoint,field=('images','image') if kind=='image' else ('files','file')
    data={'image_type':'message'} if kind=='image' else {'file_type':'stream','file_name':path.name}
    mime='image/png' if kind=='image' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    with path.open('rb') as f:
        response=requests.post(api.BASE+'/im/v1/'+endpoint,headers={'Authorization':'Bearer '+api._tenant_token()},data=data,files={field:(path.name,f,mime)},timeout=60)
    payload=response.json()
    if response.status_code!=200 or payload.get('code')!=0:raise RuntimeError('Feishu media upload failed')
    return payload['data'][kind+'_key']

def deliver(bundle,api,uuid,upload_fn=upload,*,expected_account=None,expected_day=None):
    # Validate the entire bundle before even the first text or upload.
    if bundle.get('type')=='profit_bundle':
        from . import report_preflight
        report_preflight.check(bundle,expected_account=expected_account,expected_day=expected_day)
    for item in bundle['files']:
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Attachment changed after preparation')
    receipt=Path(bundle['directory'])/'delivery.json'
    state=json.loads(receipt.read_text()) if receipt.exists() else {'chat':api.chat_id,'parts':{}}
    if state['chat']!=api.chat_id:raise ValueError('Receipt bound to another chat')
    text=' '.join('<at user_id="'+a+'">投手</at>' for a in bundle['actors'])+'\n'+bundle['text']
    parts=[{'kind':'text','content':{'text':text}}]+bundle['files']
    for index,part in enumerate(parts):
        key=str(index);s=state['parts'].setdefault(key,{})
        kind=part['kind'];path=Path(part['path']) if 'path' in part else None
        if path and hashlib.sha256(path.read_bytes()).hexdigest()!=part['sha256']:raise ValueError('Attachment changed after preparation')
        if kind!='text' and 'upload_key' not in s:
            s['upload_key']=upload_fn(api,kind,path);save(receipt,state)
        content=part.get('content') or {kind+'_key':s['upload_key']}
        if 'message_id' not in s:
            if s.get('attempted'):raise AmbiguousDelivery('Previous send outcome unknown; manual readback required')
            s['attempted']=True;s['attempted_at']=time.time();save(receipt,state)
            response=api._send(kind,content,request_uuid=hashlib.sha256((uuid+':'+key).encode()).hexdigest()[:40])
            s['message_id']=response['data']['message_id'];save(receipt,state)
        if s.get('verified'):continue
        items=api.get_message(s['message_id'])
        if len(items)!=1:raise RuntimeError('Message readback missing')
        m=items[0]
        if m.get('chat_id')!=api.chat_id or m.get('msg_type')!=kind or m.get('deleted'):raise RuntimeError('Readback destination/type mismatch')
        received=json.loads(m['body']['content'])
        if kind=='text':
            if not set(bundle['actors']).issubset({x.get('id') for x in m.get('mentions',[])}):raise RuntimeError('Operator mention not verified')
            if bundle['date'] not in received.get('text',''):raise RuntimeError('Readback date missing')
        elif kind=='image':
            if received.get('image_key')!=s['upload_key']:raise RuntimeError('Image mismatch')
        else:
            if received.get('file_name')!=path.name:raise RuntimeError('Attachment name mismatch')
            response=requests.get(api.BASE+'/im/v1/messages/'+s['message_id']+'/resources/'+received['file_key'],params={'type':'file'},headers={'Authorization':'Bearer '+api._tenant_token()},timeout=60)
            if response.status_code!=200 or hashlib.sha256(response.content).hexdigest()!=part['sha256']:raise RuntimeError('Attachment hash mismatch')
        s['verified']=True;save(receipt,state)
    state['complete']=True;save(receipt,state)
    return state
