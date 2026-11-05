"""Evidence-backed human review queue and append-only notes; never clears gates."""
import hashlib,json,getpass,socket,uuid
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .daily_refresh import atomic,write

def source_proof(daily_home,reference):
 if not reference or ':sha256:' not in reference:return {'verified':False,'reason':'missing_hash_reference'}
 name,sha=reference.split(':sha256:',1)
 if Path(name).name!=name:return {'verified':False,'reason':'invalid_reference'}
 daily_home=Path(daily_home)
 candidates=list(daily_home.glob('batch-*/'+name))+list(daily_home.parent.parent.glob('history-*/'+name))
 for path in sorted(candidates):
  if hashlib.sha256(path.read_bytes()).hexdigest()==sha:return {'verified':True,'path':str(path),'sha256':sha}
 return {'verified':False,'reason':'matching_original_not_found','sha256':sha}

def save_queue(home,daily_home,packets):
 home=Path(home);home.mkdir(parents=True,exist_ok=True);items=[]
 for p in packets:
  item=dict(p);item['original_evidence']={side:source_proof(daily_home,(p.get(side) or {}).get('evidence')) for side in ('before','after')}
  item['technical_evidence_ready']=all(p['checks'].values()) and all(x['verified'] for x in item['original_evidence'].values())
  item['review_status']='已审核：接受修订后数据' if p.get('review_resolution') and item['technical_evidence_ready'] else '待人审';items.append(item)
  archive=home/(p['revision_id']+'.json')
  if not archive.exists():write(archive,item)
 atomic(home/'queue.json',{'at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'revisions':items,'execution_clearance':False})
 return items

def add_note(home,revision_id,note):
 home=Path(home);queue=json.loads((home/'queue.json').read_text());packet=next((p for p in queue['revisions'] if p['revision_id']==revision_id),None)
 if packet is None or not note.strip():raise ValueError('unknown_revision_or_empty_note')
 folder=home/'notes';folder.mkdir(exist_ok=True);path=folder/(uuid.uuid4().hex+'.json')
 record={'revision_id':revision_id,'at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'local_account':getpass.getuser(),'host':socket.gethostname(),'note':note,'identity_scope':'local_process_not_business_approval','execution_clearance':False}
 with path.open('x') as f:json.dump(record,f,ensure_ascii=False,indent=2)
 path.chmod(0o600);return str(path)
