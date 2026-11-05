"""Native daily analysis, guarded budget execution and read-only ROI shadow journal."""
import fcntl,json,os,subprocess,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from qc_shadow.adapter import Source
from qc_shadow.__main__ import shadow,engine_sha
from qc_shadow.engine import VERSION
from qc_shadow.ledger import Ledger

def run(home,source_root,history_path,refresh_daily=False):
    home=Path(home).resolve();source_root=Path(source_root).resolve()
    if home==source_root or home.is_relative_to(source_root):raise ValueError('shadow_must_be_separate')
    home.mkdir(parents=True,exist_ok=True)
    os.umask(0o077)
    with (home/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'already_running'}
        at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        if refresh_daily:
            from qc_shadow.daily_refresh import ensure
            fresh=ensure(home/'daily',source_root,history_path)
            if fresh.get('revisions',0)>0 or fresh.get('status') in ('failed','revision_review_required'):
                helper=Path(__file__).with_name('daily_notice.py')
                try:
                    result=subprocess.run([sys.executable,'-B',str(helper),'--source',str(source_root),'--home',str(home)],capture_output=True,timeout=90)
                    fresh['notice_process_exit']=result.returncode
                except subprocess.TimeoutExpired:
                    fresh['notice_process_exit']='uncertain_timeout'
            if fresh['status']!='ok':
                status=dict(fresh,checked_at=at,mode='日报未通过，停止影子判断；不使用旧日报',execution_allowed=False,plan_pause_enabled=False)
                temp=home/'status.tmp';temp.write_text(json.dumps(status,ensure_ascii=False,indent=2));temp.replace(home/'status.json')
                return status
            history_path=fresh['history']
        at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        source=Source(source_root,history_path=history_path)
        snapshots=[source.snapshot(account,at) for account in source.accounts()]
        from qc_shadow.revision_resolution import apply_resolutions
        for snapshot in snapshots:apply_resolutions(snapshot,home/'control/revision-resolutions',home/'daily')
        from qc_shadow.refund_authority import apply_authority
        authority=home/'control/refund-authority-20261104.json'
        bound_authority=home/'control/refund-authority-20261104-bound.json'
        if bound_authority.exists():authority=bound_authority
        if authority.exists():
            model=json.loads((source_root/'config/rules.json').read_text()).get('model',{})
            for snapshot in snapshots:apply_authority(snapshot,json.loads(authority.read_text()),model)
        bundle={'kind':'source_shadow','rule_version':VERSION,'engine_sha256':engine_sha(),'snapshots':snapshots}
        results=[shadow(s) for s in snapshots]
        if any(r['execution_allowed'] or r['rule_live_on'] is not None for r in results):raise ValueError('shadow_boundary')
        from qc_shadow.control_evidence import record_snapshot
        readiness=[record_snapshot(home/'control',s) for s in snapshots]
        from qc_shadow.budget_cycle import run_cycle
        budget_cycle=run_cycle(home/'control',source_root,snapshots)
        from qc_shadow.roi_cycle import run_cycle as roi_run_cycle
        try:roi_status=roi_run_cycle(home/'roi',source_root,snapshots)
        except Exception as exc:
            roi_status={'status':'failed','error_type':type(exc).__name__,'roi_write_enabled':False,'error_count':1}
            from qc_shadow.daily_refresh import atomic
            (home/'roi').mkdir(exist_ok=True)
            atomic(home/'roi/latest.json',dict(roi_status,at=at,errors=[{'error_type':type(exc).__name__}]))
        if roi_status.get('error_count',0)>0:
            try:
                notice=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('roi_notice.py')),'--source',str(source_root),'--home',str(home)],capture_output=True,timeout=90)
                roi_status['notice_exit']=notice.returncode
            except subprocess.TimeoutExpired:roi_status['notice_exit']='uncertain_timeout'
        key=Ledger(home/'shadow.db').record(bundle,results)
        folder=home/'runs';folder.mkdir(exist_ok=True)
        path=folder/(key+'.json')
        if not path.exists():
            with path.open('x') as f:json.dump({'run_id':key,'input':bundle,'output':results},f,ensure_ascii=False)
        status={'status':'ok','checked_at':at,'run_id':key,'execution_allowed':False,'plan_pause_enabled':False,
                'mode':('完整日报影子分析与受控预算执行前重检；缺项阻断执行，暂停关闭' if budget_cycle['enabled'] else ('最新完整日报通过对账与修订检查后记录影子；缺项不补零' if refresh_daily else '本地证据影子记录')),
                'roi_shadow':roi_status,'roi_write_enabled':False,'daily_refresh_enabled':refresh_daily,'control_evidence_enabled':True,
                'budget_execution_enabled':budget_cycle['enabled'],'budget_attempt_count':len(budget_cycle['actions']),
                'budget_action_count':sum(a.get('status') in ('success','uncertain') for a in budget_cycle['actions']),
                'eligible_budget_plans':sum(a.get('status') in ('success','uncertain') for a in budget_cycle['actions']),
                'plans':sum(len(s['plans']) for s in snapshots)}
        temporary=home/'status.tmp';temporary.write_text(json.dumps(status,ensure_ascii=False,indent=2));temporary.replace(home/'status.json')
        return status
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--home',required=True);p.add_argument('--source',required=True);p.add_argument('--history',required=True);p.add_argument('--refresh-daily',action='store_true');a=p.parse_args()
    print(json.dumps(run(a.home,a.source,a.history,a.refresh_daily),ensure_ascii=False))
