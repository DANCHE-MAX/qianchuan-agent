"""Finite 4x15min balance trial; separate durable ledger, existing authorized notifier."""
import argparse, hashlib, json, os, sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent import account_balance, settings
from agent.balance_cycle import rate
from agent.card_view import card, text_element
from agent.notify import FeishuAppNotifier, feishu_allowed_chat_ids
from agent.reliability import file_lock
from zoneinfo import ZoneInfo
STATE = ROOT/'data/qianchuan/balance_trial_20261102.json'

def now(): return datetime.now(ZoneInfo('Asia/Shanghai'))
def save(path, state):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w',encoding='utf8') as f:
        json.dump(state,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.chmod(tmp,0o600);os.replace(tmp,path)
def credentials(env):
    account = env.get('QC_ADVERTISER_IDS','').strip()
    chat = env.get('FEISHU_ALERT_CHAT_ID') or env.get('FEISHU_CHAT_ID')
    allowed = feishu_allowed_chat_ids(env) or [env.get('FEISHU_CHAT_ID')]
    if not account.isdigit() or not chat or chat not in allowed: raise ValueError('AccountOrChatNotAllowed')
    return account,chat

def result(state):
    points=state['points']
    if len(points)!=5 or any(p.get('sample') is None for p in points):
        return {'rate':None,'reason':'四个15分钟节点存在漏采或失败，无法计算完整一小时速率'}
    stamps=[datetime.fromisoformat(p['sample']['fetched_at']) for p in points]
    if any(not 10*60 <= (b-a).total_seconds() <= 20*60 for a,b in zip(stamps,stamps[1:])):
        return {'rate':None,'reason':'采样实际间隔未满足15分钟±5分钟，测试连续性不合格'}
    return rate([p['sample'] for p in points],points[-1]['sample'],hours=1,tolerance_minutes=5)

def rendered(state,p):
    label='起始基准' if p['index']==0 else '第%d/4次采样'%p['index']
    if p.get('sample'):
        s=p['sample'];a=s['amounts_yuan'];body='实际采样：%s（北京时间）\n可用余额：%s元\n总余额：%s元\n冻结余额：%s元'%(s['fetched_at'],a['account_valid'],a['account_total'],a['account_frozen'])
    else: body='本节点未取得有效余额：'+p.get('error','MissedWindow')+'。不补造历史数据。'
    return card('千川余额15分钟测试｜'+label,[text_element(body),text_element('批次：'+state['id']+'；第4次结束后汇总约一小时余额净下降速率。')])

def summary(state):
    v=result(state);state['result']=v
    if v['rate'] is None: text='本次速率无法计算：'+v['reason']
    else: text='实际窗口：%s—%s\n实际间隔：%.4f小时\n总余额净下降：%.2f元\n平均余额净下降速率：%.2f元/小时'%(v['start'],v['end'],Decimal(v['elapsed_hours']),Decimal(v['decline']),Decimal(v['rate']))
    return card('千川余额测试｜四次采样结束',[text_element(text),text_element('这是账户余额净下降观测速率，不是精确广告消耗流水。未自动切换为每小时采样/四小时输出，待本轮结果确认。批次：'+state['id'])])

def send_item(state,item,payload,notifier,path,current):
    if item.get('delivery') in ('sent','expired','uncertain'): return
    # Crash after send is uncertain: never silently replay on next run.
    if item.get('delivery')=='sending':
        item['delivery']='uncertain';save(path,state);return
    if (current-datetime.fromisoformat(item['created_at'])).total_seconds()>1800:
        item['delivery']='expired';save(path,state);return
    item['delivery']='sending';save(path,state)
    try:
        reply=notifier.send_card(payload,request_uuid=item['uuid'])
    except Exception as e:
        item['delivery']='uncertain';item['send_error_type']=type(e).__name__;save(path,state);return
    item['delivery']='sent';item['sent_at']=now().isoformat()
    item['message_id']=reply.get('data',{}).get('message_id') if isinstance(reply,dict) else None
    save(path,state)

def item(state,index,created):
    return {'index':index,'created_at':created.isoformat(),'delivery':'pending','uuid':hashlib.sha256((state['id']+':'+str(index)).encode()).hexdigest()[:40]}

def advance(state,fetch,current):
    """At most one real request; missing slots stay missing."""
    origin=datetime.fromisoformat(state['started_at']);next_index=len(state['points'])
    while next_index<=4 and current>=origin+timedelta(minutes=15*next_index):
        due=origin+timedelta(minutes=15*next_index);p=item(state,next_index,current);p['due_at']=due.isoformat()
        if current>due+timedelta(minutes=5): p['sample']=None;p['error']='MissedWindow'
        else:
            try:
                p['sample']=fetch()
                if str(p['sample']['advertiser_id'])!=state['account']:raise ValueError('AccountMismatch')
            except Exception as e:p['sample']=None;p['error']=type(e).__name__
        state['points'].append(p);next_index+=1
    if len(state['points'])==5:state['status']='completed'
    return state

def execute(action,path=STATE):
    path.parent.mkdir(parents=True,exist_ok=True)
    with file_lock(path.with_suffix('.lock')):
        env=settings.load_env();account,chat=credentials(env)
        if action=='init':
            if path.exists():raise ValueError('TrialAlreadyExists')
            sample=account_balance.fetch(env);at=datetime.fromisoformat(sample['fetched_at'])
            state={'id':'balance15-'+at.strftime('%Y%m%dT%H%M%S'),'started_at':at.isoformat(),'account':account,'chat':chat,'status':'active','points':[]}
            p=item(state,0,at);p['sample']=sample;state['points'].append(p);save(path,state)
        else:
            state=json.loads(path.read_text())
            if (account,chat)!=(state['account'],state['chat']):raise ValueError('PinnedAccountOrChatChanged')
        if action=='status':
            print(json.dumps({'id':state['id'],'status':state['status'],'started_at':state['started_at'],'points':[{'index':p['index'],'delivery':p['delivery'],'fetched_at':(p.get('sample') or {}).get('fetched_at'),'error':p.get('error')} for p in state['points']],'result':state.get('result')},ensure_ascii=False));return
        if state['status']=='active':advance(state,lambda:account_balance.fetch(env),now());save(path,state)
        notifier=FeishuAppNotifier(env['FEISHU_APP_ID'],env['FEISHU_APP_SECRET'],chat)
        for p in state['points']:send_item(state,p,rendered(state,p),notifier,path,now())
        if state['status']=='completed':
            if 'summary' not in state:state['summary']=item(state,5,now());save(path,state)
            payload=summary(state);save(path,state);send_item(state,state['summary'],payload,notifier,path,now())
        print(json.dumps({'id':state['id'],'status':state['status'],'samples':len(state['points']),'deliveries':[p['delivery'] for p in state['points']],'summary_delivery':state.get('summary',{}).get('delivery'),'next_due':(datetime.fromisoformat(state['started_at'])+timedelta(minutes=15*len(state['points']))).isoformat() if state['status']=='active' else None},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['init','tick','status']);args=parser.parse_args()
    try:execute(args.action)
    except Exception as e:
        print(json.dumps({'error_type':type(e).__name__},ensure_ascii=False));sys.exit(1)
