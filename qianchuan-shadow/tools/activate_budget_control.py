"""One-time, already-authorized activation; reads/fixes B, never changes platform budgets."""
import argparse,json,hashlib,fcntl
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from qc_shadow.read_transport import ReadClient
from qc_shadow.daily_refresh import Paced
from qc_shadow.execution_store import ExecutionStore
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--home',required=True);args=p.parse_args()
home=Path(args.home);source=Path(args.source);home.mkdir(parents=True,exist_ok=True)
authority=json.loads((home/'refund-authority-20261104.json').read_text());client=ReadClient.from_runtime(source)
if client.account!=authority['account_id']:raise ValueError('account_mismatch')
store=ExecutionStore(home/'execution.db');path=home/'activation.json'
with (home.parent/'worker.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if path.exists():raise ValueError('already_activated_do_not_refreeze')
 with store.db() as db:
  if db.execute('SELECT count(*) FROM baselines').fetchone()[0] or db.execute('SELECT count(*) FROM intents').fetchone()[0]:raise ValueError('existing_control_state_requires_recovery_not_reset')
 paced=Paced(client);start=datetime.now(ZoneInfo('Asia/Shanghai'));captured=[];folder=home/'activation-evidence';folder.mkdir(exist_ok=True)
 for pid in authority['plans']:
  r=paced.get(client.DETAIL,{'advertiser_id':client.account,'ad_id':int(pid)});d=r['data'];setting=d['delivery_setting']
  if str(d['ad_id'])!=pid or d.get('marketing_goal')!='VIDEO_PROM_GOODS' or d.get('adlab_scene')!=1 or setting.get('budget_mode')!='BUDGET_MODE_DAY':raise ValueError('baseline_scope_or_mode_invalid')
  from qc_shadow.history import amount
  budget=amount(setting['budget'])
  if budget<=0:raise ValueError('baseline_positive_required')
  at=datetime.now(ZoneInfo('Asia/Shanghai'));record={'account_id':client.account,'plan_id':pid,'at':at.isoformat(),'response':r}
  data=json.dumps(record,ensure_ascii=False,sort_keys=True).encode();sha=hashlib.sha256(data).hexdigest();f=folder/(pid+'-'+sha+'.json');f.write_bytes(data);f.chmod(0o600)
  captured.append((client.account,pid,str(budget),str(start.date()),str(f)+':sha256:'+sha));print(pid,'verified',str(budget),flush=True)
 end=datetime.now(ZoneInfo('Asia/Shanghai'))
 if start.date()!=end.date():raise ValueError('activation_crossed_day')
 activation={'authorized':True,'account_id':client.account,'plans':list(authority['plans']),'live_on':str(start.date()),'activated_at':end.isoformat(),'baseline_read_started_at':start.isoformat(),
  'historical_policy':'replay_only_current_decisions','no_prior_protection_or_recovery':True,
  'state_basis':'first_controlled_execution_launch; prior worker shadow-only, no actual pause/resume or budget intents; historical actions not replayed',
  'budget_rule':'fixed_B/3; cap_2.5B; floor_0.01; all_gates_required','plan_pause_enabled':False,'authorization_evidence':'user_chat_20261104_explicit_conditional_budget_execution'}
 # All baseline rows commit together; never edit or reset an existing B.
 with store.db() as db:
  db.execute('BEGIN IMMEDIATE')
  db.executemany('INSERT INTO baselines VALUES(?,?,?,?,?)',captured)
  db.execute('INSERT INTO observations(account,plan,at,payload,hash) VALUES(?,?,?,?,?)',(client.account,'__activation__',end.isoformat(),json.dumps(activation,ensure_ascii=False),hashlib.sha256(json.dumps(activation,sort_keys=True).encode()).hexdigest()))
 with path.open('x') as f:json.dump(activation,f,ensure_ascii=False,indent=2)
 path.chmod(0o600)
 print('activated',len(captured),'plans; platform writes=0; pause=false')
