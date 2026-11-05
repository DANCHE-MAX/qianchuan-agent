"""Validate and atomically import Grok's report. No ad API calls or Feishu sends."""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--input',required=True)
    parser.add_argument('--date',help='操作日期YYYY-MM-DD，默认北京时间昨天')
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    root=Path(args.project).resolve()
    sys.path.insert(0,str(root))
    from agent import operation_report as op,settings,hourly
    now=hourly.now_china();day=args.date or (now.date()-timedelta(days=1)).isoformat()
    env=settings.load_env(root/'config/.env');account=hourly.account_id(env)
    path=Path(args.input)
    if path.stat().st_size>8*1024*1024:raise ValueError('输入汇报文件过大')
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    clean=op.validate(value,account,day,now)
    if args.check_only:
        print('校验通过：账户%s，操作日期%s，%d项记录；未写入、未发送或执行任何投放操作。'%(account,day,len(clean['actions'])))
    else:
        result=op.write(root,clean,account,day,now)
        print('已保存操作汇报：'+str(result))
        print('仅写入汇报文件；未发送飞书或执行投放操作。')


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,TypeError,KeyError):
        raise SystemExit('操作汇报导入失败：请核对账户、日期、字段及时间；未输出原始内容或密钥。')
