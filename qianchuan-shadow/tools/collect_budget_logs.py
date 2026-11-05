"""Bounded GET-only budget evidence; captures all pages, no retries or state edits."""
import json
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from qc_shadow.read_transport import ReadClient,ReadFailure
from qc_shadow.readiness import pagination_audit
client=ReadClient.from_runtime('/Users/example/Library/Application Support/CANOTWAIT/QianchuanAgent')
f=Path('evidence/readiness-v3/private');now=datetime.now(ZoneInfo('Asia/Shanghai'))
for detail in sorted(f.glob('detail-*.json')):
 pid=detail.stem.removeprefix('detail-');out=f/('logs-'+pid+'.json')
 if out.exists():raise FileExistsError(out)
 rec={'account_id':client.account,'plan_id':pid,'query_start':(now-timedelta(days=179)).strftime('%Y-%m-%d 00:00:00'),'query_end':now.strftime('%Y-%m-%d %H:%M:%S'),'fetched_at':now.isoformat(),'pages':[],'pages_complete':False}
 try:
  for page in range(1,51):
   r=client.get(client.LOGS,{'advertiser_id':client.account,'object_type':'AD','object_id':int(pid),'start_time':rec['query_start'],'end_time':rec['query_end'],'page':page,'page_size':20});rec['pages'].append(r)
   info=r['data']['page_info']
   if page>=info['total_page']:rec['reached_last_page']=True;break
 except ReadFailure as e:rec['error']={'code':e.code,'http':e.http,'request_id':e.request_id}
 rec['pagination_audit']=pagination_audit(rec['pages']);rec['pages_complete']=rec['pagination_audit']['completeness_verified']
 with out.open('x') as h:json.dump(rec,h,ensure_ascii=False,indent=2)
 out.chmod(0o600);print(pid,'pages',len(rec['pages']),'complete',rec['pages_complete'],flush=True)
