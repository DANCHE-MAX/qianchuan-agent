"""Run independently of the listener. Default read/check only; --send sends incidents."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import health, settings
from agent.notify import FeishuAppNotifier


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--send',action='store_true')
    args = parser.parse_args(); root = Path(args.project).resolve()
    env = settings.load_env(root/'config/.env')
    factory = lambda chat: FeishuAppNotifier(env['FEISHU_APP_ID'],env['FEISHU_APP_SECRET'],chat)
    try:
        result = health.check(root,env,factory,send=args.send)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if result['issues'] else 0
    except Exception as exc:
        print('健康检查失败（%s）；请核对配置、磁盘权限及网络；未输出密钥。' % type(exc).__name__)
        return 2


if __name__ == '__main__': raise SystemExit(main())
