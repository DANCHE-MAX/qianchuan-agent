"""Apply explicit, exact-revision user decisions to verified snapshot projections.

Preserves original history and all unrelated gates. Never authorizes an ad write.
"""
import json,hashlib
from pathlib import Path
from .plan_audit import revision_id,revision_packets
from .revision_review import source_proof

def apply_resolutions(snapshot,home,daily_home):
 home=Path(home);pending=[];reviewed=[];errors=[]
 rows=[dict(r,plan_id=p['plan_id']) for p in snapshot['plans'] for r in p.get('daily',[])]
 for revision in snapshot.get('pending_execution_revisions',[]):
  key=revision_id(revision);path=home/(key+'.json');accepted=False
  if path.exists():
   try:
    raw=path.read_bytes();decision=json.loads(raw)
    if (decision.get('account_id')!=snapshot['account_id'] or decision.get('revision_id')!=key or
        decision.get('decision')!='accept_revised_facts' or decision.get('authorization_source')!='explicit_user_chat' or
        not all(decision.get(k) for k in ('reason','authorized_at','user_instruction'))):raise ValueError('resolution_scope_or_authority_invalid')
    packet=revision_packets([revision],rows)[0]
    if not all(packet['checks'].values()):raise ValueError('current_facts_no_longer_match_reviewed_revision')
    if not all(source_proof(daily_home,(revision.get(side) or {}).get('evidence'))['verified'] for side in ('before','after')):raise ValueError('original_revision_proof_unverified')
    reviewed.append({'revision':revision,'resolution_file':str(path),'resolution_sha256':hashlib.sha256(raw).hexdigest(),'decision':decision,'review_status':'已审核：接受修订后数据','execution_authorized':False});accepted=True
   except (ValueError,KeyError,TypeError,OSError) as exc:errors.append({'revision_id':key,'reason':str(exc)})
  if not accepted:pending.append(revision)
 snapshot['pending_execution_revisions']=pending
 snapshot['reviewed_execution_revisions']=reviewed
 if errors:snapshot['revision_resolution_errors']=errors
 if not pending:snapshot['gaps']=[g for g in snapshot.get('gaps',[]) if g!='data_revision_pending_execution_review']
 return snapshot
