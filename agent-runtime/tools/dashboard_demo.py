"""Create explicitly fictional, isolated dashboard fixtures; refuses existing targets."""
import sys,json,sqlite3,hashlib
from pathlib import Path
from datetime import date,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import plan_profit,profit_policy

def create(root):
 root=Path(root)
 if root.exists():raise ValueError('Demo target must not exist')
 (root/'data/qianchuan').mkdir(parents=True);(root/'data/model_policy').mkdir()
 auth={'account':'demo','status':'CONFIRMED','version':'fictional-demo-v1','overall_rate':'.6','plan_refunds':{'demo-a':{'rate':'.5','band':'demo','source':'fictional'}}}
 (root/'data/model_policy/authority.json').write_text(json.dumps(auth))
 base=dict(refund_rate='.6',purchase_multiplier='3.2',fee_rate='.1310',ad_rate='.15',shipping_mode='excluded');policy=profit_policy.load_policy(root,'demo',base)
 with sqlite3.connect(root/'data/qianchuan/daily_brief.db') as db:
  db.execute('CREATE TABLE jobs(account TEXT,day TEXT,payload TEXT,sent INTEGER)')
  for i in range(7):
   day=str(date(2026,1,7)-timedelta(days=i));folder=root/'out/profit_daily/demo'/day;folder.mkdir(parents=True)
   raw=[dict(plan_id='demo-a',name='演示商品A',date=day,gmv=str(1000+i*100),spend='100'),dict(plan_id='demo-b',name='演示商品B',date=day,gmv='100',spend=str(50+i*5)),dict(plan_id='demo-c',name='全店托管',date=day,gmv='200',spend='10')]
   r=plan_profit.report([],raw,[],policy,date=day);r.update(account='demo',policy=policy)
   (folder/'result.json').write_text(json.dumps(r,ensure_ascii=False));(folder/'two_day').mkdir()
   mapping=[dict(day=day,account='demo',rows=[dict(plan=x['plan_id'],product=x['plan_id'],name=x['name']) for x in raw])];(folder/'two_day/collected.json').write_text(json.dumps(mapping,ensure_ascii=False))
   bundle=dict(directory=str(folder),date=day,account='demo',files=[],input_hashes={f:hashlib.sha256((folder/f).read_bytes()).hexdigest() for f in ['result.json','two_day/collected.json']})
   db.execute('INSERT INTO jobs VALUES(?,?,?,1)',('demo',day,json.dumps(bundle)))
if __name__=='__main__':create(Path(sys.argv[1]))
