"""Human feedback and read-only budget comparison; never imports budget writers."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
import json,re
from .feedback_store import Ledger
from .notify import feishu_allowed_chat_ids

ACTIONS={'采纳':'adopted','修改后采纳':'modified','暂缓':'deferred','拒绝':'rejected','已执行':'self_reported'}
LABELS={v:k for k,v in ACTIONS.items()}
MODES={'BUDGET_MODE_DAY','BUDGET_MODE_TOTAL'}
ID=r'FB-[0-9a-f]{20}'


def clock():return datetime.now(timezone.utc)
def stamp(value):
    result=datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(result,datetime) or result.tzinfo is None:raise ValueError('时间须为带时区的ISO格式')
    return result.astimezone(timezone.utc)
def money(value):
    try:
        result=Decimal(str(value))
        if not result.is_finite() or result<0 or result>Decimal('1000000000000'):raise ValueError()
        if result!=result.quantize(Decimal('.01')):raise ValueError()
        return format(result,'f')
    except (InvalidOperation,TypeError,ValueError):raise ValueError('金额须为非负有限数，最多两位小数') from None

def parse(command):
    parts=command.strip().split(maxsplit=2)
    if len(parts)!=3 or parts[0]!='反馈' or not re.fullmatch(ID,parts[1]):raise ValueError('反馈格式无效，请使用帮助中的编号与格式')
    rest=parts[2].split(maxsplit=1);action=rest[0];tail=rest[1] if len(rest)>1 else ''
    if action not in ACTIONS:raise ValueError('反馈状态无效')
    value={'action':ACTIONS[action],'note':tail,'amount':None,'executed_at':None}
    if action=='修改后采纳':
        bits=tail.split(maxsplit=1)
        if len(bits)!=2 or not bits[1].strip():raise ValueError('修改后采纳需要金额和原因')
        value.update(amount=money(bits[0]),note=bits[1])
    elif action=='已执行':
        bits=tail.split(maxsplit=2)
        if len(bits)!=3 or not bits[2].strip():raise ValueError('已执行需要金额、带时区操作时间和说明')
        value.update(amount=money(bits[0]),executed_at=stamp(bits[1]).isoformat(),note=bits[2])
    elif action in ('暂缓','拒绝') and not tail.strip():raise ValueError('请补充暂缓或拒绝原因')
    if len(value['note'])>1000:raise ValueError('反馈说明最多1000字')
    return parts[1],value


class Service:
    def __init__(self,ledger,cfg,env):
        self.ledger=ledger;self.env=env
        self.account=str(env.get('QC_ADVERTISER_IDS','')).strip()
        self.chat=env.get('FEISHU_ALERT_CHAT_ID') or env.get('FEISHU_CHAT_ID')
        self.actors=sorted(set(cfg.get('notify',{}).get('roles',{}).get('投手',[])))
        if (not self.account.isdigit() or not self.chat or self.chat not in set(feishu_allowed_chat_ids(env))
            or not self.actors or any(not re.fullmatch(r'ou_[A-Za-z0-9]+',x) for x in self.actors)):
            raise ValueError('当前账户、目标群白名单或投手身份配置不完整')

    def scoped(self,identity):
        p=self.ledger.get(identity)
        if p['account']!=self.account or p['chat']!=self.chat or p['actors']!=self.actors:raise ValueError('建议与当前账户、群或投手配置不一致')
        return p

    def create(self,spec,now=None):
        now=stamp(now or clock());snap=stamp(spec['snapshot_at'])
        if snap>now:raise ValueError('数据截止时间不能在未来')
        plan=str(spec.get('plan_id') or '')
        if plan and not plan.isdigit():raise ValueError('计划ID无效')
        mode=spec.get('budget_mode');amount=spec.get('target_budget')
        if mode is not None and mode not in MODES:raise ValueError('须明确日预算或总预算，历史可追加广告费不是日预算')
        if amount is not None and mode not in MODES:raise ValueError('金额缺少预算类型')
        missing=spec.get('missing',[])
        if not isinstance(missing,list) or any(not isinstance(x,str) or not x.strip() for x in missing):raise ValueError('缺失清单格式无效')
        if not plan:missing=[*missing,'计划映射缺失']
        if mode is None or amount is None:missing=[*missing,'目标预算或预算类型缺失']
        for key in ('style','model_version'):
            if not isinstance(spec.get(key),str) or not spec[key].strip():raise ValueError('缺少款号或模型版本')
        payload={'account':self.account,'chat':self.chat,'actors':self.actors,'style':spec['style'],'plan_id':plan or None,
                 'snapshot_at':snap.isoformat(),'model_version':spec['model_version'],'budget_mode':mode,
                 'target_budget':money(amount) if amount is not None else None,'missing':sorted(set(missing)),
                 'revision':str(spec.get('revision','1')),'issued_at':now.isoformat(),'expires_at':(min(now,snap)+timedelta(hours=24)).isoformat()}
        return self.ledger.create(payload)

    def record(self,message,now=None):
        now=stamp(now or clock())
        try:
            text=json.loads(message['body']['content'])['text']
            for mention in message.get('mentions',[]):
                key=mention.get('key','')
                if re.fullmatch(r'@_user_\d+',key):text=text.replace(key,'').strip()
            identity,value=parse(text);p=self.scoped(identity);sender=message['sender']
            created=datetime.fromtimestamp(int(message['create_time'])/1000,timezone.utc)
            if (message.get('msg_type')!='text' or message.get('deleted') is not False or message.get('updated') is not False
                or sender.get('sender_type')!='user' or sender.get('id_type')!='open_id' or sender.get('id') not in self.actors
                or message.get('chat_id')!=self.chat or not self.ledger.receipt(identity)
                or message.get('parent_id')!=self.ledger.receipt(identity) or not stamp(p['issued_at'])<=created<=now
                or not re.fullmatch(r'om_[A-Za-z0-9]+',message.get('message_id',''))):raise ValueError('消息身份、群、原建议引用或时间核验失败')
            if value['action'] in ('adopted','modified') and (now>=stamp(p['expires_at']) or p['missing']):
                raise ValueError('建议已过期或数据不全，请先更新测算；不能作为可投额度')
            if value['executed_at'] and not stamp(p['issued_at'])<=stamp(value['executed_at'])<=created:raise ValueError('自报操作时间超出有效范围')
            return self.ledger.record(identity,dict(value,proposal_id=identity,message_id=message['message_id'],actor=sender['id'],created=created.isoformat()))
        except (KeyError,TypeError,OverflowError,json.JSONDecodeError):raise ValueError('消息字段不完整，未记录反馈') from None

    def verify(self,identity,now=None,fetch=None):
        now=stamp(now or clock());p=self.scoped(identity);events=self.ledger.events(identity);event=events[-1] if events else None
        result={'status':'unverified','checked_at':now.isoformat(),'observed_amount':None,'reason':'尚无有效自报执行记录',
                'execution_proven':False,'budget_write_allowed':False}
        if not event or event['action']!='self_reported':return result
        if not p['plan_id'] or p['budget_mode'] not in MODES:return dict(result,reason='计划映射或预算类型缺失')
        try:
            if fetch is None:
                from .budgets import fetch
            data=fetch(self.env,self.account,[{'plan_id':p['plan_id']}])
            rows=data.get('plans',[])
            if data.get('advertiser_id')!=self.account or len(rows)!=1:raise ValueError()
            row=rows[0];at=stamp(row['fetched_at'])
            if row.get('plan_id')!=p['plan_id'] or row.get('status')!='ok' or row.get('mode')!=p['budget_mode']:raise ValueError()
            if not stamp(event['executed_at'])<=at<=now+timedelta(seconds=30) or at<now-timedelta(minutes=5):raise ValueError()
            observed=money(row['amount'])
            result.update(observed_amount=observed,snapshot_at=at.isoformat(),
                          status='matched' if Decimal(observed)==Decimal(event['amount']) else 'mismatch',
                          reason='仅比较当前预算设置，不证明操作者、操作历史或实际花费')
        except Exception:
            result.update(reason='预算读取失败、字段缺失、类型不符或快照过期；待核验')
        return self.ledger.add_check(identity,event['message_id'],result)

    def detail(self,identity):
        p=self.scoped(identity);events=self.ledger.events(identity)
        return {'proposal':p,'delivery':self.ledger.delivery_state(identity),'events':events,
                'check':self.ledger.latest_check(identity,events[-1]['message_id']) if events else None,
                'outcomes':{'2h':'待补同口径经营数据','24h':'待补同口径经营数据','final_profit':'待售后成熟及费用核验'}}

    def listing(self):
        result=[]
        for p in self.ledger.all():
            if p['account']==self.account and p['chat']==self.chat and p['actors']==self.actors:result.append(self.detail(p['id']))
        return result


def render(detail,show_delivery=True):
    p=detail['proposal'];events=detail['events'];latest=events[-1] if events else None
    lines=[f"{p['id']}｜{p['style']}｜计划{p['plan_id'] or '待补'}",f"截止：{p['snapshot_at']}；模型：{p['model_version']}",f"建议有效至：{p['expires_at']}",
           f"目标：{p['budget_mode'] or '待补'} {p['target_budget'] if p['target_budget'] is not None else '待补'}元",
           '缺失项：'+('；'.join(p['missing']) or '登记时未列出（不代表最终结算完成）'),
           '反馈：'+(LABELS[latest['action']] if latest else '待反馈')+f"；修订{len(events)}次"]
    if show_delivery:lines.append('发送状态：'+detail['delivery'])
    if latest:
        lines.append(f"说明：{latest['note']}；反馈时间：{latest['created']}")
        if latest['action']=='self_reported':lines.append(f"投手自报：{latest['amount']}元；操作时间：{latest['executed_at']}")
    check=detail['check']
    lines.append('只读核验：'+({'matched':'上次读取时设置一致','mismatch':'上次读取时设置不一致','unverified':'待核验'}[check['status']] if check else '尚未核验'))
    if check:lines.append('上次读取金额：'+str(check['observed_amount'] if check['observed_amount'] is not None else '待补')+'；核验时间：'+check['checked_at']+'；'+check['reason'])
    lines.extend(['2h/24h经营效果：待补同口径数据；最终利润：待售后成熟。',
                  '采纳不等于执行；当前设置一致不证明由该投手操作，不是已花广告费或保本保证。仅记录/读取，不调整预算。'])
    return '\n'.join(lines)

HELP='''反馈入口（需引用对应建议并@机器人）：
反馈 编号 采纳
反馈 编号 修改后采纳 金额 原因
反馈 编号 暂缓 原因
反馈 编号 拒绝 原因
反馈 编号 已执行 金额 2026-11-05T16:00:00+08:00 操作说明
核验反馈 编号 / 反馈详情 编号 / 反馈列表
金额必须与建议预算类型相同。仅记录与只读核验，不自动调整预算。'''


def matches(command):
    return command.strip().startswith(('反馈','核验反馈'))


def publish(service,identity,notifier):
    if (service.ledger.path.parent.parent/'config/feedback.paused').exists():
        return {'status':'paused','id':identity}
    p=service.scoped(identity)
    state=service.ledger.delivery_state(identity)
    if state!='draft':return {'status':state,'id':identity}
    if not service.ledger.reserve_delivery(identity):return {'status':service.ledger.delivery_state(identity),'id':identity}
    # A single request, never notifier._send (which retries ambiguous sends).
    text=render(service.detail(identity),show_delivery=False)+'\n'+HELP+'\n'+' '.join('<at user_id="'+x+'">投手</at>' for x in p['actors'])
    try:
        response=notifier._request('POST',notifier.BASE+'/im/v1/messages?receive_id_type=chat_id',
                                  {'receive_id':p['chat'],'msg_type':'text','content':json.dumps({'text':text},ensure_ascii=False),
                                   'uuid':identity},headers={'Authorization':'Bearer '+notifier._tenant_token()})
        mid=response.get('data',{}).get('message_id')
        if response.get('code')!=0 or not re.fullmatch(r'om_[A-Za-z0-9]+',mid or ''):raise ValueError('发送回执不完整')
        service.ledger.finish_delivery(identity,mid)
        return {'status':'sent','id':identity,'message_id':mid}
    except Exception:return {'status':'uncertain','id':identity,'reason':'发送结果不确定，禁止自动重发；请人工核对群消息'}


def authorized_message(notifier,mid,service,now):
    if not re.fullmatch(r'om_[A-Za-z0-9]+',mid):raise ValueError('消息编号无效')
    try:
        response=notifier._request('GET',notifier.BASE+'/im/v1/messages/'+mid+'?user_id_type=open_id',
                                   headers={'Authorization':'Bearer '+notifier._tenant_token()})
    except Exception:raise ValueError('消息回读失败，未记录反馈') from None
    rows=response.get('data',{}).get('items',[])
    if response.get('code')!=0 or len(rows)!=1:raise ValueError('消息无法回读核验')
    m=rows[0];sender=m.get('sender',{})
    if (m.get('message_id')!=mid or m.get('chat_id')!=service.chat or m.get('msg_type')!='text'
        or m.get('deleted') is not False or m.get('updated') is not False
        or sender.get('id') not in service.actors or sender.get('id_type')!='open_id' or sender.get('sender_type')!='user'):
        raise ValueError('消息身份、群或有效性核验失败')
    created=datetime.fromtimestamp(int(m['create_time'])/1000,timezone.utc)
    if created>now or now-created>timedelta(hours=24):raise ValueError('查询消息已过期或时间无效')
    return m


def reply(notifier,message_id,command,root,env,context):
    from .settings import load_config
    root=Path(root);context=context or {}
    if (root/'config/feedback.paused').exists():return None
    cfg=load_config(root/'config/rules.json')
    account=str(env.get('QC_ADVERTISER_IDS','')).strip()
    chat=env.get('FEISHU_ALERT_CHAT_ID') or env.get('FEISHU_CHAT_ID')
    actors=cfg.get('notify',{}).get('roles',{}).get('投手',[])
    if (context.get('chat_id')!=chat or context.get('sender_id') not in actors
        or context.get('advertiser_id')!=account or chat not in set(feishu_allowed_chat_ids(env))):return None
    try:
        service=Service(Ledger(root/'data/feedback_loop.db'),cfg,env);now=clock()
        m=authorized_message(notifier,message_id,service,now)
        # Use API-verified content, never the caller's suggested command.
        text=json.loads(m['body']['content'])['text']
        for mention in m.get('mentions',[]):
            key=mention.get('key','')
            if re.fullmatch(r'@_user_\d+',key):text=text.replace(key,'').strip()
        text=text.strip()
        if text=='反馈帮助':answer=HELP
        elif text=='反馈列表':
            items=service.listing();pending=sum(not i['events'] for i in items)
            answer=f'共{len(items)}条建议，待反馈{pending}条。\n'+'\n'.join(
                i['proposal']['id']+'｜'+i['proposal']['style']+'｜'+(LABELS[i['events'][-1]['action']] if i['events'] else '待反馈') for i in items[:30])
            if len(items)>30:answer+='\n仅显示最近30条；按编号查询详情。'
        elif re.fullmatch(r'(反馈详情|核验反馈) '+ID,text):
            identity=text.split()[1]
            if text.startswith('核验反馈'):service.verify(identity,now=now)
            answer=render(service.detail(identity))
        elif text.startswith('反馈 '):
            event=service.record(m,now=now);answer='反馈已记录。\n'+render(service.detail(event['proposal_id']))
        else:answer=HELP
    except ValueError as exc:answer='未完成：'+str(exc)+'\n'+HELP
    except Exception:answer='反馈读取或核验暂不可用，请稍后重试；未调整预算。'
    return notifier.reply_text(message_id,answer)
