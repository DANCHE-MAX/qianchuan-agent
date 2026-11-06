"""Explicit dated image reports; independent inbound ledger, no scheduled sends."""
import hashlib,json,shutil,sqlite3
from datetime import datetime
from pathlib import Path
from . import settings,profit_daily
from .profit_summary import deliver
from .conversation import Conversation
from .card_view import answer_card

def obtain(root,outdir,env,day):
    dbpath=root/'data/qianchuan/daily_brief.db'
    if dbpath.exists():
        with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute('SELECT payload FROM jobs WHERE account=? AND day=? AND sent=1',(env['QC_ADVERTISER_IDS'],day)).fetchone()
        if row:
            bundle=json.loads(row[0]);directory=Path(bundle['directory']).resolve()
            if not directory.is_relative_to(Path(outdir).resolve()/'profit_daily'/env['QC_ADVERTISER_IDS']/day):raise ValueError('Source scope mismatch')
            report=json.loads((directory/'result.json').read_text())
            if report.get('date')!=day or str(report.get('account'))!=env['QC_ADVERTISER_IDS']:raise ValueError('Report scope mismatch')
            for image in (x for x in bundle['files'] if x['kind']=='image'):
                if hashlib.sha256(Path(image['path']).read_bytes()).hexdigest()!=image['sha256']:raise ValueError('Source image changed')
            return bundle
    from .generation import CHINA
    return profit_daily.prepare(root,env,datetime.now(CHINA),settings.load_config(root/'config/rules.json'),{},day=day)

def completed(outdir,account,day):
    path=Path(outdir).resolve().parent/'data/generation.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT payload FROM profit_image_jobs WHERE account=? AND day=? AND done=1 ORDER BY rowid DESC LIMIT 1',(str(account),day)).fetchone()
    if not row:raise ValueError('No completed exact-date image report')
    result=json.loads(row[0])['report']
    if (result.get('date')!=day or str(result.get('account'))!=str(account)
        or result.get('version')!='qianchuan-plan-scenario-v1'
        or not isinstance(result.get('rows'),list) or not isinstance(result.get('totals_calculable_only'),dict)):
        raise ValueError('Image report scope mismatch')
    return result

def reply(api,mid,command,root,outdir,env,context,memory=None):
    from .generation import generation_lock,target_date,_uuid
    account=env.get('QC_ADVERTISER_IDS','').strip();chat=context['chat_id']
    with generation_lock(root/'data/generation.lock'):
        with sqlite3.connect(root/'data/generation.db') as db:
            db.execute('CREATE TABLE IF NOT EXISTS profit_image_jobs(message_id TEXT PRIMARY KEY,account TEXT,chat TEXT,day TEXT,payload TEXT,done INTEGER DEFAULT 0)')
            job=db.execute('SELECT account,chat,day,payload,done FROM profit_image_jobs WHERE message_id=?',(mid,)).fetchone()
            if job and (job[0],job[1])!=(account,chat):raise ValueError('Inbound report scope mismatch')
            if not job:
                try:day=target_date(context,command)
                except ValueError as exc:return api.reply_card(mid,answer_card(str(exc),title='投流助手｜日期提示'),request_uuid=_uuid(mid,0))
                db.execute('INSERT INTO profit_image_jobs(message_id,account,chat,day) VALUES(?,?,?,?)',(mid,account,chat,day));db.commit()
                job=(account,chat,day,None,0)
            day=job[2];payload=json.loads(job[3]) if job[3] else None
            if payload is None:
                try:
                    bundle=obtain(root,outdir,env,day)
                    report=json.loads((Path(bundle['directory'])/'result.json').read_text())
                    if report.get('date')!=day or str(report.get('account'))!=account:raise ValueError('Generated report scope mismatch')
                    directory=Path(outdir)/'profit_generation'/hashlib.sha256((account+':'+mid).encode()).hexdigest()[:32]
                    directory.mkdir(parents=True,exist_ok=True);files=[]
                    for i,item in enumerate(x for x in bundle['files'] if x['kind']=='image'):
                        target=directory/('report-%02d.png'%i);shutil.copy2(item['path'],target);files.append(str(target.resolve()))
                    if not files:raise ValueError('No image pages')
                    payload={'report':report,'files':files}
                except Exception as exc:
                    payload={'report':None,'files':[],'error_type':type(exc).__name__}
                db.execute('UPDATE profit_image_jobs SET payload=? WHERE message_id=?',(json.dumps(payload,ensure_ascii=False),mid));db.commit()
            if payload['report'] is None:
                return api.reply_card(mid,answer_card('%s日报图片生成失败，数据待核验；未使用旧日期或旧模型替代。'%day,title='投流助手｜日报生成失败'),request_uuid=_uuid(mid,0))
            for path in payload['files']:deliver(api,mid,chat,Path(path))
            db.execute('UPDATE profit_image_jobs SET done=1 WHERE message_id=?',(mid,));db.commit()
            conversation=memory or Conversation(root/'data/conversation.db',account=account)
            conversation.save(chat,context.get('sender_id'),[{'question':command[:2000],'answer':'已生成%s新版日报图片。'%day,
                'source':json.dumps({'资料类型':'新版利润日报','数据日期':day,'报告':payload['report']},ensure_ascii=False),'report_snapshot':None,'display_kind':'answer'}])
            return payload['report']
