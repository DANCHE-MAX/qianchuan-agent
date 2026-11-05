"""Preview or explicitly apply the user's previously confirmed seasonal scenarios."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import seasonal_refunds, trading_context


def main():
    parser=argparse.ArgumentParser(description='季节整体退款情景配置；默认预览，--apply备份后启用。')
    parser.add_argument('--project',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--account',required=True)
    parser.add_argument('--season',required=True,choices=('summer','autumn_winter'))
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        result=seasonal_refunds.configure(args.project,args.account,args.season,apply=args.apply)
        if args.apply:
            result['context']=str(trading_context.export(args.project,args.account,question='按已确认季节整体退款情景核对利润与保本ROI')/'context.json')
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except Exception as error:
        print('季节策略配置或导出未完成：'+type(error).__name__+'；请核对本机原始退款表与规则。若已应用而导出失败，规则备份仍保留。未发飞书或执行投放。',file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
