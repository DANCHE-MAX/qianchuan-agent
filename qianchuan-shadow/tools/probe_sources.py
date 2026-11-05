"""One-shot authorized GET probes; never print or persist authentication."""
import json,os
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from qc_shadow.read_transport import ReadClient,ReadFailure
root=Path('/Users/example/Library/Application Support/CANOTWAIT/QianchuanAgent');client=ReadClient.from_runtime(root)
out=Path('evidence/source-v2/private');out.mkdir(mode=0o700,exist_ok=True)
now=datetime.now(ZoneInfo('Asia/Shanghai'));day=now.date();end=day-timedelta(days=1)
def save(name,payload):
 p=out/(name+'.json')
 with p.open('x') as f:json.dump(payload,f,ensure_ascii=False,indent=2)
 p.chmod(0o600)
def probe(name,path,params):
 try:
  result=client.get(path,dict(advertiser_id=client.account,**params));save(name,result)
  print(name,'code=0','data_keys',list(result.get('data',{})));return result
 except ReadFailure as e:
  result={'code':e.code,'http':e.http,'request_id':e.request_id};save(name,result);print(name,result);return result
config=probe('report-config',client.CONFIG,{'data_topics':['OVERALL_ROI_PRODUCT_PROJECT']})
inventory=probe('plan-list',client.LIST,{'marketing_goal':'VIDEO_PROM_GOODS','adlab_scene':'OVERALL_PROJECT','filtering':{'status':'ALL'},'start_time':str(end)+' 00:00:00','end_time':str(end)+' 23:59:59','page':1,'page_size':100})
ads=(inventory.get('data') or {}).get('ad_list',[])
print('inventory_page_info',(inventory.get('data') or {}).get('page_info'),'ad_info_keys',list(ads[0]['ad_info']) if ads else [])
report=probe('daily-report-7d',client.REPORT,{'data_topic':'OVERALL_ROI_PRODUCT_PROJECT','dimensions':['ad_id','ad_name','stat_time_day'],'metrics':['stat_cost_for_roi2','total_pay_order_gmv_include_coupon_for_roi2','total_pay_order_count_for_roi2'],'filters':[],'order_by':[],'start_time':str(end-timedelta(days=6))+' 00:00:00','end_time':str(end)+' 23:59:59','page':1,'page_size':100})
print('daily_page_info',(report.get('data') or {}).get('page_info'))
if ads:
 ad=ads[0]['ad_info'];pid=ad['id']
 probe('plan-detail',client.DETAIL,{'ad_id':pid})
 probe('operation-logs',client.LOGS,{'object_type':'AD','object_id':pid,'start_time':(now-timedelta(days=179)).strftime('%Y-%m-%d 00:00:00'),'end_time':now.strftime('%Y-%m-%d %H:%M:%S'),'page':1,'page_size':20})
print('requests',len(client.receipts))
