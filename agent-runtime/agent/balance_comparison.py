"""Three validated balance-decline windows and durable image-card delivery."""
import copy,hashlib,json,subprocess
from datetime import timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path
from .balance_cycle import rate,stamp
from .hourly import CHINA
from .profit_daily import save
from .profit_delivery import upload,AmbiguousDelivery

def percent(current,reference):
    if current is None or reference is None or Decimal(reference)==0:return None
    return format((Decimal(current)/Decimal(reference)-1)*100,'f')

def build(samples,account,now,current):
    if now.tzinfo is None:raise ValueError('Aware time required')
    end=now.astimezone(CHINA).replace(hour=(now.astimezone(CHINA).hour//2)*2,minute=0,second=0,microsecond=0)
    valid=[]
    for s in samples:
        try:
            if str(s['advertiser_id'])==str(account):stamp(s['fetched_at']);valid.append(s)
        except (KeyError,TypeError,ValueError):pass
    rows=[];available=None
    for i,(label,target) in enumerate([('本两小时',end),('前两小时',end-timedelta(hours=2)),('昨日同时段',end-timedelta(days=1))]):
        candidates=[s for s in valid if abs((stamp(s['fetched_at'])-target).total_seconds())<=300]
        selected=current if i==0 else min(candidates,key=lambda s:abs((stamp(s['fetched_at'])-target).total_seconds())) if candidates else None
        try:
            if selected is None or str(selected['advertiser_id'])!=str(account) or abs((stamp(selected['fetched_at'])-target).total_seconds())>300:raise ValueError()
            result=rate(valid,selected,hours=2)
            if i==0:
                amount=Decimal(str(selected['amounts_yuan']['account_valid']))
                if amount.is_finite():available=str(amount)
        except (KeyError,TypeError,ValueError,InvalidOperation):result={'rate':None,'reason':'缺少有效的时段结束余额样本'}
        rows.append({'label':label,'window':(target-timedelta(hours=2)).strftime('%m-%d %H:%M')+'—'+target.strftime('%H:%M'),
                     'rate':format(Decimal(result['rate']),'f') if result['rate'] is not None else None,'reason':result.get('reason'),'actual_start':result.get('start'),'actual_end':result.get('end')})
    speed=rows[0]['rate'];runway=None
    if available is not None:
        if Decimal(available)<=0:runway='0'
        elif speed is not None and Decimal(speed)>=Decimal('.01'):runway=str(Decimal(available)/Decimal(speed))
    return {'account':str(account),'end':end.isoformat(),'rows':rows,'available':available,'runway':runway,
            'previous_change':percent(speed,rows[1]['rate']),'yesterday_change':percent(speed,rows[2]['rate'])}

def attach(root,store,account,now,current,card):
    # Reuse the existing local rendering configuration, never create account config.
    if not (Path(root)/'config/profit_daily.json').exists():return card
    samples=store.balances(account,now-timedelta(hours=29))
    result=copy.deepcopy(card);result['_balance_comparison']=build(samples,account,now,current)
    return result

def render(root,view,path):
    cfg=json.loads((Path(root)/'config/profit_daily.json').read_text())
    save(path.with_suffix('.json'),view)
    subprocess.run([cfg['render_python'],str(Path(root)/'tools/balance_comparison_image.py'),str(path.with_suffix('.json')),str(path),cfg['font']],check=True,capture_output=True,timeout=45)
    if not path.exists():raise ValueError('Renderer did not produce image')

def deliver(root,api,event,card,uuid):
    directory=Path(root)/'out/balance_comparison'/hashlib.sha256((api.chat_id+':'+event).encode()).hexdigest()[:32]
    directory.mkdir(parents=True,exist_ok=True);path=directory/'comparison.png';receipt=directory/'delivery.json'
    if not path.exists():render(root,card['_balance_comparison'],path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    state=json.loads(receipt.read_text()) if receipt.exists() else {'chat':api.chat_id,'event':event,'sha256':digest}
    if state['chat']!=api.chat_id or state['event']!=event or state['sha256']!=digest:raise ValueError('Balance image receipt mismatch')
    if 'upload_key' not in state:
        state['upload_key']=upload(api,'image',path);save(receipt,state)
    if 'message_id' not in state:
        if state.get('attempted'):raise AmbiguousDelivery('Balance image send outcome unknown; readback required')
        sent=copy.deepcopy(card);sent.pop('_balance_comparison',None)
        mentions=[e for e in sent.get('elements',[]) if '<at ' in json.dumps(e)]
        sent['elements']=[{'tag':'img','img_key':state['upload_key'],'alt':{'tag':'plain_text','content':'余额速率对比'},'mode':'fit_horizontal'}]+mentions
        state['attempted']=True;save(receipt,state)
        result=api.send_card(sent,request_uuid=uuid);state['message_id']=result['data']['message_id'];save(receipt,state)
    if not state.get('verified'):
        items=api.get_message(state['message_id'])
        if len(items)!=1:raise RuntimeError('Balance image readback missing')
        m=items[0]
        if m.get('chat_id')!=api.chat_id or m.get('msg_type')!='interactive' or m.get('deleted'):raise RuntimeError('Balance image readback mismatch')
        if state['upload_key'] not in m.get('body',{}).get('content',''):raise RuntimeError('Balance image key not confirmed')
        state['verified']=True;save(receipt,state)
    return state
