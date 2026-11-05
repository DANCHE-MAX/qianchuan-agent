"""Explicit one-shot shadow/replay or local-only serving; no automatic deployment."""
import argparse,hashlib,json,os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .adapter import Source
from .contracts import Evaluation
from .engine import evaluate,VERSION
from .daily_rules import assess_daily
from .ledger import Ledger,canonical
DEFAULT_SOURCE='/Users/example/Library/Application Support/CANOTWAIT/QianchuanAgent'

def engine_sha():
    return hashlib.sha256(b''.join(p.name.encode()+p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))).hexdigest()

def output_path(path,source):
    path=Path(path).resolve()
    if any(path.is_relative_to(root) for root in (Path(source).resolve(),Path(DEFAULT_SOURCE).resolve())):raise ValueError('outputs_must_be_outside_production')
    return path

def shadow(snapshot):
    results=[]
    for p in snapshot['plans']:
        refund=p.get('refund') or {}
        x=Evaluation(account_id=snapshot['account_id'],plan_id=p['plan_id'],scope=p['scope'],as_of=snapshot['as_of'],
            first_enabled_on=p.get('first_enabled_on'),first_enabled_evidence=p.get('first_enabled_evidence',''),
            daily=[{k:r.get(k) for k in ('day','spend','gmv','orders','complete','evidence')} for r in p.get('daily',[])],
            refund_rate=refund.get('rate'),refund_band=refund.get('band'),refund_verified=refund.get('verified',False),model_version=refund.get('model_version'))
        r=evaluate(x);r.update(account_id=x.account_id,plan_id=x.plan_id,source_gaps=p['missing'])
        r['independent_daily_rules']=assess_daily(p.get('daily',[]),snapshot['as_of'],refund)
        results.append(r)
    return {'rule_version':VERSION,'engine_sha256':engine_sha(),'execution_allowed':False,'rule_live_on':None,
            'decisions':results,'source_gaps':snapshot['gaps'],'coverage':'source_read_only_shadow_not_complete_daily_validation'}

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('shadow');s.add_argument('--source',default=DEFAULT_SOURCE);s.add_argument('--history');s.add_argument('--output',default='evidence/live-shadow.json');s.add_argument('--ledger',default='evidence/shadow.db')
    e=sub.add_parser('evaluate');e.add_argument('input');e.add_argument('--ledger',default='evidence/shadow.db')
    r=sub.add_parser('replay');r.add_argument('run_id');r.add_argument('--ledger',default='evidence/shadow.db')
    a=sub.add_parser('serve');a.add_argument('--source',default=DEFAULT_SOURCE);a.add_argument('--history');a.add_argument('--port',type=int,default=8767)
    p=sub.add_parser('profit',help='Offline order-profit SHADOW; no platform operations')
    p.add_argument('--xlsx',required=True);p.add_argument('--profile',required=True);p.add_argument('--as-of',required=True)
    p.add_argument('--database',default='evidence/profit.db');p.add_argument('--output-dir',default='reports/profit');p.add_argument('--returns')
    args=parser.parse_args()
    if args.command=='profit':
        from .profit_import import run_profit
        result=run_profit(args.xlsx,args.profile,output_path(args.database,DEFAULT_SOURCE),output_path(args.output_dir,DEFAULT_SOURCE),args.as_of,args.returns)
        print(canonical(result));return
    if args.command=='serve':
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(args.source,os.environ.get('QC_SHADOW_API_TOKEN',''),history_path=args.history),host='127.0.0.1',port=args.port,access_log=False);return
    if args.command=='shadow':
        source=Source(args.source,history_path=args.history);at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        snapshots=[source.snapshot(account,at) for account in source.accounts()]
        bundle={'kind':'source_shadow','rule_version':VERSION,'engine_sha256':engine_sha(),'snapshots':snapshots}
        results=[shadow(s) for s in snapshots]
        ledger=Ledger(output_path(args.ledger,args.source));key=ledger.record(bundle,results)
        out=output_path(args.output,args.source);out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x',encoding='utf8') as f:json.dump({'run_id':key,'input':bundle,'output':results},f,ensure_ascii=False,indent=2)
        os.chmod(out,0o600)
        print(canonical({'run_id':key,'accounts':len(snapshots),'plans':sum(len(s['plans']) for s in snapshots),'execution_allowed':False,'output':str(out)}))
    elif args.command=='evaluate':
        x=Evaluation.model_validate_json(Path(args.input).read_text());bundle={'kind':'contract_evaluation','engine_sha256':engine_sha(),'input':x.model_dump(mode='json')}
        result=evaluate(x);key=Ledger(output_path(args.ledger,DEFAULT_SOURCE)).record(bundle,result)
        print(canonical({'run_id':key,'result':result}))
    else:
        path=output_path(args.ledger,DEFAULT_SOURCE)
        if not path.is_file():raise FileNotFoundError('ledger_missing')
        entry=Ledger(path).get(args.run_id);bundle=entry['input']
        if bundle.get('engine_sha256')!=engine_sha():raise ValueError('engine_version_changed_use_recorded_version')
        result=[shadow(s) for s in bundle['snapshots']] if bundle['kind']=='source_shadow' else evaluate(Evaluation.model_validate(bundle['input']))
        if canonical(result)!=canonical(entry['output']):raise ValueError('replay_mismatch')
        print(canonical({'run_id':args.run_id,'replay':'identical','execution_allowed':False}))

if __name__=='__main__':main()
