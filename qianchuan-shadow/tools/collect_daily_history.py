"""Acquire official closed-day facts and separate daily aggregates, no zero filling."""
import json,time
from pathlib import Path
from datetime import datetime,timedelta,date
from zoneinfo import ZoneInfo
from qc_shadow.read_transport import ReadClient,ReadFailure
folder=Path('evidence/source-v2/private');ads=json.loads((folder/'plan-list-attempt2.json').read_text())['data']['ad_list']
c=ReadClient.from_runtime('/Users/example/Library/Application Support/CANOTWAIT/QianchuanAgent');now=datetime.now(ZoneInfo('Asia/Shanghai'))
start=min(date.fromisoformat(a['ad_info']['create_time'][:10]) for a in ads);last=now.date()-timedelta(days=1)
while start<=last:
 end=min(start+timedelta(days=29),last)
 for granularity,dimensions in [('plan',['ad_id','ad_name','stat_time_day']),('day',['stat_time_day'])]:
  params={'advertiser_id':c.account,'data_topic':'OVERALL_ROI_PRODUCT_PROJECT','dimensions':dimensions,'metrics':['stat_cost_for_roi2','total_pay_order_gmv_include_coupon_for_roi2','total_pay_order_count_for_roi2'],'filters':[],'order_by':[],'start_time':str(start)+' 00:00:00','end_time':str(end)+' 23:59:59','page_size':100}
  pages=[];expected=None;complete=False
  for page in range(1,101):
   try:s=c.get(c.REPORT,dict(params,page=page))
   except ReadFailure as e:s={'code':e.code,'http':e.http,'request_id':e.request_id}
   pages.append(s)
   if s.get('code')!=0:break
   info=s['data']['page_info'];pair=(info['total_number'],info['total_page'])
   if expected is None:expected=pair
   if pair!=expected or info['page']!=page:break
   if page>=max(1,info['total_page']):
    complete=sum(len(p['data']['rows']) for p in pages)==info['total_number'];break
   time.sleep(.3)
  value={'account_id':c.account,'requested_start':str(start),'requested_end':str(end),'granularity':granularity,'params':params,'fetched_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'pages_complete':complete,'pages':pages}
  p=folder/('daily-'+str(start)+'-'+granularity+'.json')
  with p.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2)
  p.chmod(0o600);print(start,end,granularity,'pages',len(pages),'complete',complete,'code',pages[-1].get('code'),flush=True);time.sleep(.3)
 start=end+timedelta(days=1)
