import unittest
from unittest.mock import Mock
from datetime import date
from qc_shadow.collection_scope import START, ACCOUNT, floor_day, scoped_changes
from qc_shadow.read_transport import ReadClient
from qc_shadow.gap_recovery import requested_gaps
class ScopeTests(unittest.TestCase):
 def test_floor_scoped(self):
  self.assertEqual(floor_day(ACCOUNT,date(2026,7,1)),START)
  self.assertEqual(floor_day('other',date(2026,7,1)),date(2026,7,1))
 def test_transport_blocks_before_token(self):
  token=Mock();c=ReadClient(ACCOUNT,token)
  for route in (c.REPORT,c.LOGS):
   with self.assertRaisesRegex(ValueError,'collection_start'):c.get(route,{'advertiser_id':ACCOUNT,'start_time':'2026-10-21 00:00:00','end_time':'2026-11-03 23:59:59'})
  token.assert_not_called()
 def test_scope_removal_not_revision(self):
  row=lambda d:dict(plan_id='1',day=d,spend='1',gmv='2',orders=1)
  self.assertEqual(scoped_changes([row('2026-10-20'),row('2026-10-22')],[row('2026-10-22')],ACCOUNT),[])
 def test_gap_queries_exclude_old_days(self):
  gaps=requested_gaps([{'plan_id':'1','first_enabled_on':'2026-10-20','daily':[]}],'2026-10-24T12:00:00+08:00',ACCOUNT)
  self.assertEqual(gaps,{'1':['2026-10-22','2026-10-23']})
 def test_migration_rebuild_preserves_first_enable_revision_and_archive(self):
  import json,tempfile,hashlib
  from pathlib import Path
  from datetime import datetime
  from unittest.mock import patch
  from test_history import dailybundle,logbundle
  from qc_shadow.history_build import derive,verify_normalized
  from qc_shadow.daily_refresh import ensure
  def bundle(start,end,kind):
   b=dailybundle(granularity=kind);b.update(account_id=ACCOUNT,requested_start=str(start),requested_end=str(end),fetched_at='2026-11-04T12:00:00+08:00')
   b['pages'][0]['data']['rows'][0]['dimensions']['stat_time_day']['ValueStr']=str(end)
   return b
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);seed=root/'seed';seed.mkdir();home=root/'home';home.mkdir();source=root/'source';(source/'config').mkdir(parents=True);(source/'config/rules.json').write_text('{"model":{}}')
   logs=logbundle();logs['account_id']=ACCOUNT
   files={'logs-1.json':json.dumps(logs).encode(),'overlap-check.json':json.dumps(bundle('2026-10-29','2026-11-03','plan')).encode()}
   for start,end in [('2026-10-01','2026-10-20'),('2026-10-22','2026-10-23'),('2026-10-29','2026-11-03')]:
    for kind in ('plan','day'):files['daily-'+start+'-'+kind+'.json']=json.dumps(bundle(start,end,kind)).encode()
   for name,raw in files.items():(seed/name).write_bytes(raw)
   old=derive(files);old.update(version='source-history-v3');path=seed/'normalized-history.json';path.write_text(json.dumps(old));original=path.read_bytes()
   pending={'plan_id':'1','day':'2026-10-20','before':{},'after':{}}
   old['revision_events_pending_execution_review']=[pending];path.write_text(json.dumps(old));original=path.read_bytes()
   (home/'daily_status.json').write_text(json.dumps({'status':'failed','target_day':'2026-11-03','attempts':3}))
   c=Mock();c.account=ACCOUNT;calls=[]
   def fetch(client,start,end,kind,now):
    calls.append((str(start),str(end)));return bundle(start,end,kind)
   with patch('qc_shadow.daily_refresh.ReadClient.from_runtime',return_value=c),patch('qc_shadow.daily_refresh.acquire',side_effect=fetch):
    result=ensure(home,source,path,datetime.fromisoformat('2026-11-04T12:00:00+08:00'))
   self.assertEqual(result['status'],'ok',result)
   self.assertTrue(all(start>='2026-10-22' for start,end in calls))
   h=json.loads(Path(result['history']).read_text());self.assertTrue(all(r['day']>='2026-10-22' for r in h['daily']))
   self.assertEqual(h['first_enabled']['1']['first_enabled_at'][:10],'2026-10-01')
   self.assertIn(pending,h['revision_events_pending_execution_review'])
   self.assertEqual(path.read_bytes(),original)
   verify_normalized(h,{r['file']:(Path(result['history']).parent/r['file']).read_bytes() for r in h['evidence_files']})
