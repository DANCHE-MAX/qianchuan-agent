"""Validated Grok action-report intake. Reads reports; never operates advertising APIs."""
import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import hourly
from .card_view import text_element

STATUS = {'success':'成功', 'failed':'失败', 'unconfirmed':'待确认'}
ACTIONS = {'budget_change':'调整预算', 'roi_change':'调整ROI目标', 'pause_plan':'暂停计划',
           'resume_plan':'恢复计划', 'create_plan':'新建计划', 'delete_plan':'删除计划',
           'material_change':'变更素材', 'other':'其他操作'}
SENSITIVE = re.compile(r'(?i)(access_token|refresh_token|app_secret|api_key|authorization)\s*[=:]')


def text(value, name, limit, required=True):
    if not isinstance(value, str) or len(value)>limit or (required and not value.strip()):
        raise ValueError('操作汇报字段无效：'+name)
    if SENSITIVE.search(value): raise ValueError('操作汇报不能包含凭证字段')
    return value.strip()


def instant(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None: raise ValueError('操作时间必须带时区')
    return stamp.astimezone(hourly.CHINA)


def validate(value, account, day, now=None):
    now = now or hourly.now_china()
    if now.tzinfo is None: raise ValueError('校验时间必须带时区')
    now = now.astimezone(hourly.CHINA)
    end = date.fromisoformat(day)
    if end>=now.date(): raise ValueError('只接受已结束的操作日期')
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value.get('schema_version') != 1:
        raise ValueError('操作汇报版本必须为1')
    if value.get('advertiser_id') != account or value.get('report_date') != day:
        raise ValueError('操作汇报账户或日期不符')
    generated = instant(text(value.get('generated_at'), 'generated_at', 50))
    if generated.date()<=end or generated>now+timedelta(minutes=5):
        raise ValueError('汇报生成时间必须在操作日结束后，且不能来自未来')
    complete=value.get('complete')
    if type(complete) is not bool: raise ValueError('必须声明操作记录是否完整')
    summary=text(value.get('summary',''), 'summary', 2000, required=False)
    source=text(value.get('source','Grok Bot'), 'source', 100)
    actions=value.get('actions')
    if not isinstance(actions,list) or len(actions)>5000: raise ValueError('操作列表无效或过大')
    normalized=[];seen=set()
    for item in actions:
        if not isinstance(item,dict):raise ValueError('操作记录必须为对象')
        identity=text(item.get('action_id'), 'action_id', 120)
        if identity in seen:raise ValueError('操作记录ID重复，不能重复计数')
        seen.add(identity)
        stamp=instant(text(item.get('executed_at'), 'executed_at', 50))
        if stamp.date()!=end or stamp>generated:raise ValueError('操作执行时间不属于汇报日期')
        kind,status=item.get('action_type'),item.get('status')
        if kind not in ACTIONS or status not in STATUS:raise ValueError('操作类型或执行状态无效')
        target_type=item.get('target_type')
        if target_type not in ('plan','material','account'):raise ValueError('操作对象类型无效')
        target_id=text(item.get('target_id'), 'target_id', 100)
        if not target_id.isascii() or not target_id.isdigit():raise ValueError('操作对象ID必须是数字字符串')
        evidence=text(item.get('evidence',''), 'evidence', 500, required=False)
        if status=='success' and not evidence:raise ValueError('成功操作必须提供日志或执行结果依据')
        normalized.append({'action_id':identity,'executed_at':stamp.isoformat(),'action_type':kind,
            'target_type':target_type,'target_id':target_id,'target_name':text(item.get('target_name'), 'target_name', 200),
            'before':text(item.get('before',''), 'before', 300, required=False),
            'after':text(item.get('after',''), 'after', 300, required=False),
            'status':status,'reason':text(item.get('reason',''), 'reason', 500, required=False),'evidence':evidence})
    normalized.sort(key=lambda x:(x['executed_at'],x['action_id']))
    return {'schema_version':1,'advertiser_id':account,'report_date':day,'generated_at':generated.isoformat(),
            'complete':complete,'source':source,'summary':summary,'actions':normalized}


def report_path(root, account, day):
    if not account.isascii() or not account.isdigit():raise ValueError('操作汇报账户ID无效')
    if date.fromisoformat(day).isoformat()!=day:raise ValueError('操作汇报日期格式无效')
    return Path(root)/'data/grok_operations'/account/(day+'.json')


def write(root, value, account, day, now=None):
    clean=validate(value,account,day,now)
    target=report_path(root,account,day);target.parent.mkdir(parents=True,exist_ok=True)
    staging=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with staging.open('x',encoding='utf-8') as output:
            json.dump(clean,output,ensure_ascii=False,indent=2);output.flush();os.fsync(output.fileno())
        os.replace(staging,target)
    finally:
        if staging.exists():staging.unlink()
    return target


def load(root, account, day, now=None):
    path=report_path(root,account,day)
    if not path.exists():return {'state':'missing','report_date':day,'message':'尚未收到Grok昨日操作汇报，不代表昨日无操作。'}
    try:
        if path.stat().st_size>8*1024*1024:raise ValueError('汇报文件过大')
        raw=path.read_bytes();clean=validate(json.loads(raw),account,day,now)
        return {'state':'ready','report':clean,'sha256':hashlib.sha256(raw).hexdigest(),'source_file':str(path.resolve())}
    except (OSError,ValueError,TypeError,KeyError):
        return {'state':'invalid','report_date':day,'message':'Grok操作汇报未通过账户、日期或记录校验，本次未纳入；经营数据照常汇报。'}


def elements(bundle, max_actions=3):
    bundle=bundle or {'state':'missing','message':'尚未收到Grok昨日操作汇报，不代表昨日无操作。'}
    if bundle.get('state')!='ready':return [text_element('Grok昨日操作：'+bundle['message'])]
    report=bundle['report'];actions=report['actions']
    counts={key:sum(a['status']==key for a in actions) for key in STATUS}
    coverage='完整记录' if report['complete'] else '部分记录，不能代表全部操作'
    lines=['Grok昨日操作｜%s：成功%d项、失败%d项、待确认%d项。' %
           (coverage,counts['success'],counts['failed'],counts['unconfirmed'])]
    if not actions:
        lines.append('已收到Grok核对后的空操作记录。' if report['complete'] else '当前提供的部分日志未列出操作，不等于昨日无操作。')
    if report['summary']:lines.append('Grok摘要：'+report['summary'][:240])
    # Surface failed/unconfirmed actions first, then the most recent successes.
    selected=sorted(actions,key=lambda a:({'failed':0,'unconfirmed':1,'success':2}[a['status']],-instant(a['executed_at']).timestamp()))[:max_actions]
    for action in selected:
        change=(action['before'] or '未提供')+' → '+(action['after'] or '未提供')
        if action['status']!='success':change+='（尝试/目标值，不代表已生效）'
        lines.append('%s %s：%s（%s），%s。' % (STATUS[action['status']],ACTIONS[action['action_type']],
            action['target_name'][:50],action['target_id'],change))
    if len(actions)>len(selected):lines.append('仅展示%d/%d项，完整记录已随日报存档。'%(len(selected),len(actions)))
    lines.append('操作状态依据Grok上报日志，日报程序未独立执行核验；不把操作与成交变化直接认定为因果关系。')
    return [text_element('\n'.join(lines))]
