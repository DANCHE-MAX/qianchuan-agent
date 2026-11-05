"""Account-pinned, paced GET-only adapter for the last execution check."""
import json,hashlib
from pathlib import Path
from decimal import Decimal
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .read_transport import ReadClient
from .daily_refresh import Paced
from .history import complete_pages,amount
CHINA=ZoneInfo('Asia/Shanghai')
class PreflightClient(ReadClient):
 BALANCE='/open_api/v1.0/qianchuan/account/balance/get/'
 ALLOWED=ReadClient.ALLOWED|{BALANCE}
class LiveReader:
 def __init__(self,source,home,client=None,clock=None):
  self.client=client or PreflightClient.from_runtime(source);self.paced=Paced(self.client);self.home=Path(home)
  self.home.mkdir(parents=True,exist_ok=True);self.clock=clock or (lambda:datetime.now(CHINA))
 def capture(self,plan):
  start=self.clock();a=self.client.account;raw=[]
  def get(path,params):
   try:result=self.paced.get(path,dict(advertiser_id=a,**params))
   except Exception as exc:
    failure={'account_id':a,'plan_id':plan,'started_at':start.isoformat(),'endpoint':path,'params':params,'code':getattr(exc,'code',None),'http':getattr(exc,'http',None),'request_id':getattr(exc,'request_id',None),'error_type':type(exc).__name__}
    data=json.dumps(failure,ensure_ascii=False,sort_keys=True).encode();failure_path=self.home/('failure-'+hashlib.sha256(data).hexdigest()+'.json')
    if not failure_path.exists():failure_path.write_bytes(data);failure_path.chmod(0o600)
    raise
   raw.append({'path':path,'params':params,'response':result});return result
  detail=get(ReadClient.DETAIL,{'ad_id':int(plan)})['data']
  if str(detail.get('ad_id'))!=plan:raise ValueError('detail_identity')
  cutoff=self.clock();params={'data_topic':'OVERALL_ROI_PRODUCT_PROJECT','dimensions':['ad_id'],'metrics':['stat_cost_for_roi2'],
   'filters':[],'order_by':[],'start_time':str(cutoff.date())+' 00:00:00','end_time':cutoff.strftime('%Y-%m-%d %H:%M:%S'),'page_size':100}
  pages=[]
  for n in range(1,101):
   r=get(ReadClient.REPORT,dict(params,page=n));pages.append(r)
   if n>=max(1,r['data']['page_info']['total_page']):break
  rows=complete_pages({'pages_complete':True,'pages':pages},'rows')
  found=[r for r in rows if str(r['dimensions']['ad_id']['Value'])==plan]
  if len(found)>1:raise ValueError('duplicate_plan_report')
  spend=str(amount(found[0]['metrics']['stat_cost_for_roi2']['Value'])) if found else None
  balance=get(self.client.BALANCE,{})['data']
  if str(balance.get('advertiser_id'))!=a:raise ValueError('balance_identity')
  units=Decimal(str(balance.get('account_valid')))
  if not units.is_finite() or units!=units.to_integral_value():raise ValueError('balance_unit')
  log_start=start.date()-timedelta(days=2);log_end=self.clock();pages=[]
  for n in range(1,101):
   r=get(ReadClient.LOGS,{'object_type':'AD','object_id':int(plan),'start_time':str(log_start)+' 00:00:00','end_time':log_end.strftime('%Y-%m-%d %H:%M:%S'),'page':n,'page_size':20});pages.append(r)
   if n>=max(1,r['data']['page_info']['total_page']):break
  logs=complete_pages({'pages_complete':True,'pages':pages},'logs')
  end=self.clock();payload={'account_id':a,'plan_id':plan,'started_at':start.isoformat(),'at':end.isoformat(),'raw':raw}
  data=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode();key=hashlib.sha256(data).hexdigest();path=self.home/(key+'.json')
  with path.open('xb') as f:f.write(data)
  path.chmod(0o600)
  return {'account_id':a,'plan_id':plan,'started_at':start.isoformat(),'at':end.isoformat(),'detail':detail,
   'today_spend':spend,'available':str(units/100000),'log_start':str(log_start),'logs_complete':True,'recent_logs':logs,'evidence':str(path)+':sha256:'+key}
 def readback(self,account,plan):
  if account!=self.client.account:raise ValueError('readback_account')
  result=self.paced.get(ReadClient.DETAIL,{'advertiser_id':account,'ad_id':int(plan)})['data']
  if str(result.get('ad_id'))!=plan or result['delivery_setting'].get('budget_mode')!='BUDGET_MODE_DAY':raise ValueError('readback_identity_or_mode')
  return str(amount(result['delivery_setting']['budget']))
