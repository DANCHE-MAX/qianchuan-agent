"""Run on the bot host before a Grok analysis; no credentials needed."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import trading_context


def main():
    parser=argparse.ArgumentParser(description='生成四项统一投流上下文，仅本地只读，不发消息或操作投放。')
    parser.add_argument('--project',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--account',required=True)
    parser.add_argument('--question',default='')
    parser.add_argument('--history-days',type=int,default=30)
    parser.add_argument('--init-only',action='store_true')
    args=parser.parse_args()
    try:
        if args.init_only:
            home=trading_context.initialize(args.project,args.account)
            print(json.dumps({'initialized':str(home),'write_authorized':False},ensure_ascii=False))
        else:
            directory=trading_context.export(args.project,args.account,question=args.question,history_days=args.history_days)
            print(json.dumps({'context':str(directory/'context.json'),'prompt':str(directory/'GROK_CONTEXT_PROMPT.md'),
                              'manifest':str(directory/'manifest.json'),'write_authorized':False},ensure_ascii=False))
    except Exception as error:
        # Avoid echoing malformed source content or paths holding credentials.
        print('上下文生成失败：'+type(error).__name__+'。请本地检查规则、边界及源文件结构；未执行投放或发送。',file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
