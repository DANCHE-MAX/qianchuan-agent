"""Verified local revisions are distinct from sent reports; no sending or ledger writes."""
from pathlib import Path
import json
from .historical_evidence import inside,digest,ledger_digest

def load(root,account,day,ledger):
 root=Path(root).resolve();index=inside(root,Path('data/dashboard_reviews')/account/(day+'.json'))
 if not index.exists():return None
 v=json.loads(index.read_text())
 if v.get('account')!=account or v.get('date')!=day or v.get('status')!='reviewed_unsent' or v.get('ledger_sha256')!=ledger_digest(ledger):raise ValueError('Review scope mismatch')
 for name,sha in v['files'].items():
  if digest(inside(root,name))!=sha:raise ValueError('Review evidence changed')
 directory=inside(root,v['directory'])
 for name in ('result.json','preflight.json','two_day/collected.json'):
  if str((directory/name).relative_to(root)) not in v['files']:raise ValueError('Unbound review input')
 confirmation=root/'data/refund_confirmations/confirmed.json'
 if str(confirmation.relative_to(root)) not in v['files']:raise ValueError('Missing business confirmation binding')
 check=json.loads((directory/'preflight.json').read_text())
 if not check.get('allowed') or check.get('errors') or check.get('date')!=day:raise ValueError('Review not passed')
 report=json.loads((directory/'result.json').read_text())
 if str(report['account'])!=account or report['date']!=day:raise ValueError('Review report mismatch')
 report['_review_status']='已复核未发送';report['_review_note']=v['note']
 mapping=next((d for d in json.loads((directory/'two_day/collected.json').read_text()) if d['day']==day),None)
 return report,mapping
