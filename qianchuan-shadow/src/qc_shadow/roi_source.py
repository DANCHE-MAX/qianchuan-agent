"""Paced GET-only ROI context; no creative persistence or write routes."""
import json,hashlib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .read_transport import ReadClient
from .daily_refresh import Paced,atomic
CHINA=ZoneInfo('Asia/Shanghai')
class RoiReadClient(ReadClient):
 ALLOWED={ReadClient.DETAIL}
def clock():return datetime.now(CHINA).isoformat()
def capture(client,pid):
 result={'plan_id':pid,'status':'failed','fetched_at':clock()}
 try:
  response=client.get(ReadClient.DETAIL,{'advertiser_id':client.client.account,'ad_id':int(pid)})
  d=response['data'];s=d.get('delivery_setting') or {}
  if str(d.get('ad_id'))!=pid or d.get('marketing_goal')!='VIDEO_PROM_GOODS':raise ValueError('detail_identity_or_scope')
  result.update(status='ok',fetched_at=clock(),name=d.get('name') or d.get('ad_name'),target_roi=s.get('roi2_goal'),budget=s.get('budget'),budget_mode=s.get('budget_mode'),platform_status=d.get('status'),opt_status=d.get('opt_status'),
   product_ids=[str(x.get('product_id')) for x in d.get('product_infos',[])],detail_request_id=response.get('request_id'))
  for k in ('smart_bid_type','alliance_commision_switch','star_task_material_switch','overall_roi_cost_items'):result[k]=s.get(k)
 except Exception as e:
  result.update(error_type=type(e).__name__,code=getattr(e,'code',None),http=getattr(e,'http',None),request_id=getattr(e,'request_id',None))
 return result

def acquire(home,source,account,pids):
 home=Path(home);home.mkdir(parents=True,exist_ok=True);now=datetime.now(CHINA);bucket=now.strftime('%Y%m%dT%H')+('00' if now.minute<30 else '30')
 scope=hashlib.sha256(json.dumps([account,sorted(pids)]).encode()).hexdigest()[:16];path=home/(bucket+'-detail-only-v2-'+scope+'.json')
 if path.exists():return json.loads(path.read_text())
 c=RoiReadClient.from_runtime(source)
 if c.account!=account:raise ValueError('account_mismatch')
 paced=Paced(c);rows=[]
 for pid in pids:rows.append(capture(paced,pid))
 result={'account_id':account,'at':clock(),'settings':rows,'receipts':paced.receipts}
 for row in rows:row['evidence']=str(path)
 atomic(path,result)
 return result
