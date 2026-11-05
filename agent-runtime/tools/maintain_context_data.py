"""Default: local diagnostics only. Explicit flags permit existing read APIs."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import context_diagnostics as diagnostics, trading_context as context, settings


def main():
    parser=argparse.ArgumentParser(description='检查最近快照间隔和历史上下文；可选只读补采与预算刷新。')
    parser.add_argument('--project',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--account',required=True)
    parser.add_argument('--collect-yesterday',action='store_true')
    parser.add_argument('--refresh-budgets',action='store_true')
    args=parser.parse_args();root=Path(args.project).resolve();now=diagnostics.clock(None)
    outcomes={};failed=False
    try:
        env=settings.load_env(root/'config/.env') if args.collect_yesterday or args.refresh_budgets else None
        for key,enabled,action in (('yesterday',args.collect_yesterday,diagnostics.collect_yesterday),
                                   ('budgets',args.refresh_budgets,diagnostics.refresh_budgets)):
            if not enabled:continue
            try:
                result=action(root,args.account,env)
                status='completed'
                if key=='budgets' and result['unavailable']:
                    failed=True;status='partial' if result['successful'] else 'failed'
                outcomes[key]={'status':status,'result':result}
            except Exception as error:
                failed=True;outcomes[key]={'status':'failed','error_type':type(error).__name__,
                                          'note':'未以0、旧时间或其他账户数据替代；请核对本地配置、来源和API权限。'}
        report=diagnostics.inspect(root,args.account)
        bundle=context.export(root,args.account,question='核对快照连续性、历史日报事实与预算时效')
        directory=context.account_home(root,args.account)/'diagnostics'
        path=directory/('check_'+now.strftime('%Y%m%d_%H%M%S')+'_'+bundle.name.rsplit('_',1)[-1]+'.json')
        context.write_json(path,{'actions':outcomes,'diagnostics':report,'context':str(bundle/'context.json')})
        print(json.dumps({'diagnostics':str(path),'context':str(bundle/'context.json'),'actions':outcomes,
                          'snapshots':report['snapshots'],'history':report['history']},ensure_ascii=False))
        return 1 if failed else 0
    except Exception as error:
        print('数据检查失败：'+type(error).__name__+'；未发送飞书或执行投放写操作。',file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
