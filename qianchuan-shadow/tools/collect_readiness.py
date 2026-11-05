"""One-shot account-pinned GET capture. Exclusive evidence writes, no live changes."""
import json,os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from qc_shadow.read_transport import ReadClient,ReadFailure
root=Path('/Users/example/Library/Application Support/CANOTWAIT/QianchuanAgent')
client=ReadClient.from_runtime(root)
folder=Path('evidence/readiness-v3/private');folder.mkdir(mode=0o700,parents=True,exist_ok=True)
source=Path('evidence/source-v2/private')
ids=[str(x['ad_info']['id']) for x in json.loads((source/'plan-list-attempt2.json').read_text())['data']['ad_list']]+['9000000000000006']
def capture(name,endpoint,params):
 p=folder/(name+'.json')
 if p.exists():raise FileExistsError(p)
 params=dict(advertiser_id=client.account,**params)
 record={'endpoint':endpoint,'params':params,'fetched_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
 try:record['response']=client.get(endpoint,params)
 except ReadFailure as e:record['response']={'code':e.code,'http':e.http,'request_id':e.request_id}
 with p.open('x') as f:json.dump(record,f,ensure_ascii=False,indent=2)
 p.chmod(0o600)
 print(name,'code',record['response'].get('code'),flush=True)
 return record['response']
products=set()
for pid in ids:
 response=capture('detail-'+pid,client.DETAIL,{'ad_id':int(pid)})
 for product in (response.get('data') or {}).get('product_infos',[]):products.add(int(product['product_id']))
for i,pid in enumerate(sorted(products)):
 capture('product-'+str(pid),client.PRODUCTS,{'filter':{'product_ids':[pid]},'page':1,'page_size':10})
print('detail_count',len(ids),'bound_product_count',len(products),flush=True)
