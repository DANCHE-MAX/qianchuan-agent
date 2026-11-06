"""Compact, deterministic interpretation images; local data never enters source code."""
import hashlib,json,subprocess
from decimal import Decimal
from pathlib import Path
from .profit_delivery import upload,AmbiguousDelivery
from .profit_daily import save

def wants_image(command):
    return (any(w in command for w in ('日报','分析','解读','总结','简单','简短'))
            and not any(w in command for w in ('详细','逐计划','全部','明细','为什么','原因','比较','对比','表格')))

def view(report):
    known=[r for r in report['rows'] if r.get('profit') is not None]
    count=report['calculable'];missing=report['missing_profit'];totals=report['totals_calculable_only']
    profit=totals.get('profit') if count else None
    spend=totals.get('spend') if count else None
    headline='利润待核验' if profit is None else ('已测算部分盈利' if Decimal(profit)>0 else '已测算部分亏损' if Decimal(profit)<0 else '已测算部分持平')
    focus=[]
    if known:
        focus.append(max(known,key=lambda r:Decimal(r['spend'])))
        rest=[r for r in known if r['plan_id']!=focus[0]['plan_id']]
        if rest:focus.append(min(rest,key=lambda r:Decimal(r['profit'])))
    return {'date':report['date'],'headline':headline,'subhead':'账户整体利润仍待核验' if missing else '全部计划已测算 · 仍为情景估算',
            'profit':profit,'spend':spend,'calculable':count,'missing':missing,
            'focus':[{'name':r.get('name') or r.get('plan_name') or '未命名计划','spend':r['spend'],'profit':r['profit']} for r in focus],
            'attention':'先核对亏损计划与缺失项，再评估投放。' if missing or any(Decimal(r['profit'])<0 for r in known) else '持续观察，历史盈利不代表今天可直接追加。'}

def deliver(api,message_id,chat,path):
    receipt=path.with_suffix('.delivery.json')
    state=json.loads(receipt.read_text()) if receipt.exists() else {'parent':message_id,'chat':chat,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    if state['parent']!=message_id or state['chat']!=chat or state['sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('Image receipt mismatch')
    if 'upload_key' not in state:
        state['upload_key']=upload(api,'image',path);save(receipt,state)
    if 'message_id' not in state:
        if state.get('attempted'):raise AmbiguousDelivery('Image reply outcome unknown; manual readback required')
        state['attempted']=True;save(receipt,state)
        result=api.reply_image(message_id,state['upload_key'],hashlib.sha256(('profit-summary:'+message_id+':'+path.name).encode()).hexdigest()[:40])
        state['message_id']=result['data']['message_id'];save(receipt,state)
    if not state.get('verified'):
        items=api.get_message(state['message_id'])
        if len(items)!=1:raise RuntimeError('Image reply readback missing')
        m=items[0]
        if m.get('chat_id')!=chat or m.get('msg_type')!='image' or m.get('deleted') or json.loads(m['body']['content']).get('image_key')!=state['upload_key']:raise RuntimeError('Image reply readback mismatch')
        state['verified']=True;save(receipt,state)
    return {'data':{'message_id':state['message_id']}}

def maybe_reply(api,message_id,chat,command,source,outdir):
    if not wants_image(command):return False,None
    try:context=json.loads(source or '{}')
    except (ValueError,TypeError):return False,None
    report=context.get('报告')
    if not isinstance(report,dict):return False,None
    root=Path(outdir).resolve().parent
    config_path=root/'config/profit_daily.json'
    if not config_path.exists():return False,None
    summary=view(report)
    directory=Path(outdir)/'profit_analysis'/str(report['account'])/report['date']/hashlib.sha256(message_id.encode()).hexdigest()[:24]
    directory.mkdir(parents=True,exist_ok=True);path=directory/'interpretation.png'
    if not path.exists():
        try:
            cfg=json.loads(config_path.read_text());save(directory/'summary.json',summary)
            subprocess.run([cfg['render_python'],str(root/'tools/profit_summary_image.py'),str(directory/'summary.json'),str(path),cfg['font']],check=True,capture_output=True,timeout=45)
            if not path.exists():raise ValueError('No image output')
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):
            # Rendering failed before any send; a short factual text is safe.
            content='%s｜%s；%s。图片暂时生成失败，请稍后重试。'%(summary['date'],summary['headline'],summary['subhead'])
            return True,api.reply_text(message_id,content)
    return True,deliver(api,message_id,chat,path)
