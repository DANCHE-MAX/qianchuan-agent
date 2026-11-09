import json,tempfile,unittest,sqlite3,socket,urllib.error
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import Mock
from agent import monitor,analysis_gate as g,request_gate
from agent.reliability import network_error

class Recovery(unittest.TestCase):
 def assessment(self,now,extra=True):
  return dict(account='synthetic',kind='plan',state='partial',reasons=['revision']+(['presence'] if extra else []),counts={'ready':1,'limited':1},cumulative_valid=True,cutoff=now.isoformat(),entities={'p':dict(name='虚构商品',revisions=[dict(metric='backend_net',before='100',after='80',decrease='20',start='2026-01-01T01:30:00+08:00',end='2026-01-01T02:00:00+08:00')])})
 def test_repeat_state_change_and_restart_keep_one_fact(self):
  with tempfile.TemporaryDirectory() as tmp:
   now=datetime.fromisoformat('2026-01-01T02:00:00+08:00');s=monitor.Store(tmp)
   for i in range(3):
    g.record(s,self.assessment(now+timedelta(minutes=i*30),i==0),now+timedelta(minutes=i*30),notify_observation=False)
    s=monitor.Store(tmp)
   with s.db() as c:self.assertEqual(c.execute("select count(*) from events where id like 'revision-v1:%'").fetchone()[0],1)
 def test_new_fact_same_status_not_lost(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=monitor.Store(tmp);now=datetime.fromisoformat('2026-01-01T02:00:00+08:00')
   a=self.assessment(now);g.record(s,a,now,notify_observation=False)
   a=self.assessment(now+timedelta(minutes=30));a['entities']['p']['revisions'][0].update(start='2026-01-01T02:00:00+08:00',end='2026-01-01T02:30:00+08:00')
   g.record(s,a,now+timedelta(minutes=30),notify_observation=False)
   with s.db() as c:self.assertEqual(c.execute('select count(*) from revision_notices').fetchone()[0],2)
 def test_migrate_sent_and_uncertain_old_event_without_resend(self):
  for status in [0,1]:
   with tempfile.TemporaryDirectory() as tmp:
    s=monitor.Store(tmp);now=datetime.fromisoformat('2026-01-01T02:00:00+08:00');a=self.assessment(now)
    with s.db() as c:
     c.execute('CREATE TABLE analysis_history(account TEXT,kind TEXT,point TEXT,fingerprint TEXT,stamp TEXT,payload TEXT,PRIMARY KEY(account,kind,point,fingerprint))')
     c.execute('INSERT INTO analysis_history VALUES(?,?,?,?,?,?)',('synthetic','plan',now.isoformat(),'partial:presence,revision',now.isoformat(),json.dumps(a)))
     c.execute('INSERT INTO events(id,account,stamp,payload,sent) VALUES(?,?,?,?,?)',('analysis-status:synthetic:plan:1','synthetic',now.isoformat(),json.dumps({'elements':[]}),status))
    g.record(s,a,now+timedelta(minutes=30),notify_observation=False)
    with s.db() as c:self.assertEqual(c.execute("select count(*) from events where id like 'revision-v1:%'").fetchone()[0],0)
 def test_shared_cooldown_pacing_and_network(self):
  with tempfile.TemporaryDirectory() as tmp:
   env={'_QC_ENV_PATH':str(Path(tmp)/'config/.env'),'QC_APP_ID':'synthetic'};now=[100.0];wait=[]
   def sleep(n):wait.append(n);now[0]+=n
   def run(op):return request_gate.run(env,op,clock=lambda:now[0],sleep=sleep,jitter=lambda:0)
   run(lambda:{'code':40100});run(lambda:{'code':40100});run(lambda:{'code':0});run(lambda:{'code':0})
   self.assertEqual(wait[:2],[5,10]);self.assertAlmostEqual(wait[2],.8)
   with self.assertRaises(urllib.error.URLError):run(Mock(side_effect=urllib.error.URLError(socket.gaierror())))
   run(lambda:{'code':0});self.assertEqual(wait[-1],5)
 def test_safe_network_categories(self):
  self.assertEqual(network_error(urllib.error.URLError(socket.gaierror('private'))),'dns')
  self.assertEqual(network_error(TimeoutError('private')),'timeout')
  self.assertIsNone(network_error(ValueError('private')))

 def test_http_throttle_shared_cooldown(self):
  with tempfile.TemporaryDirectory() as tmp:
   env={'_QC_ENV_PATH':str(Path(tmp)/'config/.env'),'QC_APP_ID':'synthetic'};now=[100.0];wait=[]
   def sleep(n):wait.append(n);now[0]+=n
   def run(op):return request_gate.run(env,op,clock=lambda:now[0],sleep=sleep,jitter=lambda:0)
   with self.assertRaises(urllib.error.HTTPError):run(Mock(side_effect=urllib.error.HTTPError('https://example.invalid',429,'synthetic',{},None)))
   run(lambda:{'code':0});self.assertEqual(wait,[5])
