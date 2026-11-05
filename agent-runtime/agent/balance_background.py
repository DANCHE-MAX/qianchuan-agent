"""Finite 4x5min background balance trial; separate durable ledger, existing authorized notifier."""
import argparse, hashlib, json, os, sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import account_balance, settings
from agent.balance_cycle import rate
from agent.card_view import card, text_element
from agent.notify import FeishuAppNotifier, feishu_allowed_chat_ids
from agent.reliability import file_lock
from zoneinfo import ZoneInfo
STATE = ROOT/'data/qianchuan/balance_background_20261102.json'

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
        return {'rate':None,'reason':'四个5分钟节点存在漏采或失败，无法计算完整20分钟速率'}
    stamps=[datetime.fromisoformat(p['sample']['fetched_at']) for p in points]
    if any(not 3*60 <= (b-a).total_seconds() <= 7*60 for a,b in zip(stamps,stamps[1:])):
        return {'rate':None,'reason':'采样实际间隔未满足5分钟±2分钟，测试连续性不合格'}
    return rate([p['sample'] for p in points],points[-1]['sample'],window_minutes=20,max_gap_minutes=7,tolerance_minutes=2)

def rendered(state,p):
    label='起始基准' if p['index']==0 else '第%d/4次采样'%p['index']
    if p.get('sample'):
        s=p['sample'];a=s['amounts_yuan'];body='实际采样：%s（北京时间）\n可用余额：%s元\n总余额：%s元\n冻结余额：%s元'%(s['fetched_at'],a['account_valid'],a['account_total'],a['account_frozen'])
    else: body='本节点未取得有效余额：'+p.get('error','MissedWindow')+'。不补造历史数据。'
    return card('千川后台5分钟测试｜'+label,[text_element(body),text_element('批次：'+state['id']+'；第4次结束后汇总约20分钟余额净下降速率。')])

def summary(state):
    v=result(state);state['result']=v
    if v['rate'] is None: text='本次速率无法计算：'+v['reason']
    else: text='实际窗口：%s—%s\n实际间隔：%.4f小时\n总余额净下降：%.2f元\n平均余额净下降速率：%.2f元/小时'%(v['start'],v['end'],Decimal(v['elapsed_hours']),Decimal(v['decline']),Decimal(v['rate']))
    return card('千川后台独立测试｜20分钟汇总',[text_element(text),text_element('这是账户余额净下降观测速率，不是精确广告消耗流水。本次是20分钟压缩测试，不是四小时实测；测试已自动结束，原有常驻调度继续。批次：'+state['id'])])

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
    while next_index<=4 and current>=origin+timedelta(minutes=5*next_index):
        due=origin+timedelta(minutes=5*next_index);p=item(state,next_index,current);p['due_at']=due.isoformat()
        if current>due+timedelta(minutes=2): p['sample']=None;p['error']='MissedWindow'
        else:
            try:
                p['sample']=fetch()
                if str(p['sample']['advertiser_id'])!=state['account']:raise ValueError('AccountMismatch')
            except Exception as e:p['sample']=None;p['error']=type(e).__name__
        state['points'].append(p);next_index+=1
    if len(state['points'])==5:state['status']='completed'
    return state

def step(engine):
    path=engine.root/'data/qianchuan/balance_background_20261102.json'
    with file_lock(path.with_suffix('.lock')):
        account,chat=credentials(engine.env)
        if path.exists():
            state=json.loads(path.read_text())
            if (account,chat)!=(state['account'],state['chat']):raise ValueError('PinnedAccountOrChatChanged')
            if state['status']=='completed' and state.get('summary',{}).get('delivery') in ('sent','uncertain','expired'):
                return True
        else:
            sample=engine.sample_balance();at=datetime.fromisoformat(sample['fetched_at'])
            state={'id':'background5-'+at.strftime('%Y%m%dT%H%M%S'),'started_at':at.isoformat(),'account':account,'chat':chat,'status':'active','points':[],'pid':os.getpid(),'driver':'V4 monitor background thread'}
            p=item(state,0,at);p['sample']=sample;state['points'].append(p);save(path,state)
        if state['status']=='active':advance(state,engine.sample_balance,now());save(path,state)
        notifier=engine.notifier_factory(chat)
        for p in state['points']:send_item(state,p,rendered(state,p),notifier,path,now())
        if state['status']=='completed':
            if 'summary' not in state:state['summary']=item(state,5,now());save(path,state)
            payload=summary(state);save(path,state);send_item(state,state['summary'],payload,notifier,path,now())
        print(json.dumps({'event':'background_balance_trial','pid':os.getpid(),'status':state['status'],'samples':len(state['points']),'deliveries':[p['delivery'] for p in state['points']],'summary_delivery':state.get('summary',{}).get('delivery')},ensure_ascii=False),flush=True)
        return state['status']=='completed'

def work(engine):
    while not engine.stopping.is_set():
        try:
            if step(engine):return
        except Exception as exc:
            print(json.dumps({'event':'background_balance_trial_error','error_type':type(exc).__name__}),flush=True)
        engine.stopping.wait(10)
