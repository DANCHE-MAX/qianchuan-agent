"""Only actionable daily failures/revisions, durable no-replay delivery state."""
import hashlib,json
from pathlib import Path

def deliver(home,status,send):
    category='revision' if status.get('revisions',0)>0 else 'failure' if status.get('status') in ('failed','revision_review_required') else None
    if category is None:return 'silent'
    identity=hashlib.sha256((status['target_day']+':'+category).encode()).hexdigest()[:40]
    folder=Path(home)/'daily_notices';folder.mkdir(exist_ok=True)
    path=folder/(identity+'.json')
    previous=json.loads(path.read_text()) if path.exists() else None
    if previous and previous['delivery']!='not_sent_preflight':return previous['delivery']
    record={'target_day':status['target_day'],'category':category,'delivery':'uncertain','request_uuid':identity}
    if previous:
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(record));temp.replace(path)
    else:
        with path.open('x') as f:json.dump(record,f)
    path.chmod(0o600)
    send(identity,category,status)
    record['delivery']='sent';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(record));temp.chmod(0o600);temp.replace(path)
    return 'sent'
