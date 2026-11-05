"""Explicit local feedback management. Nothing is sent without the send command."""
import argparse,json,sys
from pathlib import Path
from . import feedback,settings
from .feedback_store import Ledger
from .notify import FeishuAppNotifier


def main(argv=None,notifier_factory=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default=str(Path(__file__).resolve().parents[1]))
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('create',help='登记本地建议JSON并预览；不发送');create.add_argument('--file',required=True)
    for name in ('show','verify','send'):
        p=sub.add_parser(name);p.add_argument('--id',required=True)
    sub.add_parser('list')
    args=parser.parse_args(argv);root=Path(args.project)
    try:
        cfg=settings.load_config(root/'config/rules.json');env=settings.load_env(root/'config/.env')
        service=feedback.Service(Ledger(root/'data/feedback_loop.db'),cfg,env)
        if args.command=='create':
            spec=json.loads(Path(args.file).read_text(encoding='utf-8'))
            if not isinstance(spec,dict):raise ValueError('建议输入须为JSON对象')
            p=service.create(spec);print(feedback.render(service.detail(p['id'])));print('已登记；未发送。发送需显式运行send命令。')
        elif args.command=='list':
            items=service.listing();print(json.dumps(items,ensure_ascii=False,indent=2))
        elif args.command=='send':
            service.scoped(args.id)
            if not env.get('FEISHU_APP_ID') or not env.get('FEISHU_APP_SECRET'):raise ValueError('飞书应用配置不完整；未发送')
            factory=notifier_factory or FeishuAppNotifier
            notifier=factory(env['FEISHU_APP_ID'],env['FEISHU_APP_SECRET'],service.chat)
            result=feedback.publish(service,args.id,notifier);print(json.dumps(result,ensure_ascii=False))
            return 0 if result['status']=='sent' else 1
        else:
            if args.command=='verify':service.verify(args.id)
            print(feedback.render(service.detail(args.id)))
        return 0
    except ValueError as exc:
        print('未完成：'+str(exc),file=sys.stderr);return 1
    except Exception:
        print('输入、配置或读取失败；未调整预算。',file=sys.stderr);return 1
