import json,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from qc_shadow.read_transport import ReadClient,ReadFailure
folder=Path('evidence/source-v2/private');ads=json.loads((folder/'plan-list-attempt2.json').read_text())['data']['ad_list']
c=ReadClient.from_runtime('/Users/example/Library/Application Support/CANOTWAIT/QianchuanAgent');now=datetime.now(ZoneInfo('Asia/Shanghai'))
for row in ads:
 a=row['ad_info'];pages=[];complete=False;expected=None
 for page in range(1,101):
  params={'advertiser_id':c.account,'object_type':'AD','object_id':a['id'],'start_time':a['create_time'][:10]+' 00:00:00','end_time':now.strftime('%Y-%m-%d %H:%M:%S'),'page':page,'page_size':20}
  try:s=c.get(c.LOGS,params)
  except ReadFailure as e:s={'code':e.code,'http':e.http,'request_id':e.request_id}
  pages.append(s)
  if s.get('code')!=0:break
  info=s['data']['page_info'];pair=(info['total_number'],info['total_page'])
  if expected is None:expected=pair
  if pair!=expected or info['page']!=page:break
  if page>=info['total_page']:
   complete=sum(len(p['data']['logs']) for p in pages)==info['total_number'];break
  time.sleep(.25)
 out={'account_id':c.account,'plan_id':str(a['id']),'create_time':a['create_time'],'query_start':a['create_time'][:10]+' 00:00:00','query_end':now.isoformat(),'fetched_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'pages_complete':complete,'pages':pages}
 path=folder/('logs-'+str(a['id'])+'.json')
 with path.open('x') as f:json.dump(out,f,ensure_ascii=False,indent=2)
 path.chmod(0o600)
 print('plan',str(a['id'])[-4:],'pages',len(pages),'complete',complete,'code',pages[-1].get('code'),flush=True);time.sleep(.25)
