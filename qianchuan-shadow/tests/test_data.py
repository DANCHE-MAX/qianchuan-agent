import json,sqlite3,tempfile,unittest
from pathlib import Path
from qc_shadow.adapter import Source
from qc_shadow.ledger import Ledger
class DataTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  d=self.root/'data/qianchuan';d.mkdir(parents=True);(self.root/'config').mkdir()
  (self.root/'config/rules.json').write_text(json.dumps({'model':{'season':'winter','version':'v1','refund_rates':{'winter':{'x':'.5'}},'plans':{'1':{'refund_band':'x','basis':'待复核'}}}}))
  with sqlite3.connect(d/'monitor.db') as c:
   c.executescript('create table snapshots(account,kind,bucket,stamp,payload);create table balances(account,stamp,payload);')
   s={'account':'9','kind':'plan','day':'2026-11-01','fetched_at':'2026-11-01T23:30:00+08:00','unit':'CNY','scope':'乘方商品计划累计报表','rows':[{'id':'1','name':'p','ad_spend':'100000','gmv':'200000','order_count':'5'}]}
   c.execute('insert into snapshots values(?,?,?,?,?)',('9','plan','2026-11-01T23:30:00+08:00',s['fetched_at'],json.dumps(s)))
  with sqlite3.connect(d/'hourly.db') as c:c.executescript('create table hours(account,start,payload);create table plan_lists(account,payload);')
 def test_missing_daily_not_zero_or_complete(self):
  v=Source(self.root).snapshot('9','2026-11-02T10:00:00+08:00');p=v['plans'][0]
  self.assertEqual(p['observations'][-1]['spend'],'100000');self.assertEqual(p['daily'],[]);self.assertIsNone(p['first_enabled_on'])
  self.assertIsNone(v['balance']);self.assertFalse(p['refund']['verified'])
 def test_source_rejects_write(self):
  with Source(self.root).database('monitor.db') as c:
   with self.assertRaises(sqlite3.OperationalError):c.execute('delete from snapshots')
 def test_accounts_scope_no_fabrication(self):
  self.assertEqual(Source(self.root).accounts(),['9']);self.assertEqual(Source(self.root).snapshot('8','2026-11-02T10:00:00+08:00')['plans'],[])
 def test_wal_read_sees_committed_data(self):
  c=sqlite3.connect(self.root/'data/qianchuan/monitor.db');self.addCleanup(c.close);c.execute('pragma journal_mode=WAL');c.execute("insert into balances values('9','2026-11-02T09:00:00+08:00',?)",(json.dumps({'advertiser_id':'9','fetched_at':'2026-11-02T09:00:00+08:00','amounts_yuan':{'account_valid':'123','account_total':'123','account_frozen':'0'}}),));c.commit()
  self.assertEqual(Source(self.root).snapshot('9','2026-11-02T10:00:00+08:00')['balance']['amounts_yuan']['account_valid'],'123')
 def test_missing_source_never_created(self):
  with self.assertRaises(FileNotFoundError):Source(self.root/'absent').accounts()
  self.assertFalse((self.root/'absent').exists())
 def test_ledger_idempotent_and_immutable(self):
  ledger=Ledger(self.root/'shadow.db');key=ledger.record({'input':1},{'action':'observe'})
  self.assertEqual(key,ledger.record({'input':1},{'action':'observe'}));self.assertEqual(len(ledger.list()),1)
  with self.assertRaises(ValueError):ledger.record({'input':1},{'action':'pause'})
  with sqlite3.connect(ledger.path) as c:
   with self.assertRaises(sqlite3.IntegrityError):c.execute('delete from runs')
