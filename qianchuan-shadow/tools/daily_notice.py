"""Use the existing allowed notification group, never advertisement write APIs."""
import argparse,json,sys
from pathlib import Path
from qc_shadow.daily_notice import deliver
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--home',required=True);a=p.parse_args()
home=Path(a.home);status=json.loads((home/'daily/daily_status.json').read_text())
sys.path.insert(0,a.source)
from agent import settings
from agent.notify import FeishuAppNotifier,feishu_allowed_chat_ids
from agent.card_view import card,text_element
env=settings.load_env(Path(a.source)/'config/.env');chat=env.get('FEISHU_ALERT_CHAT_ID') or env.get('FEISHU_CHAT_ID')
if not chat or chat not in feishu_allowed_chat_ids(env):raise ValueError('notification_scope_invalid')
def send(identity,category,status):
    text=('发现%d处历史日报修订，新旧证据已保留；新值通过对账和重读后仅用于影子分析，预算执行待复核。'%status['revisions']) if category=='revision' else '完整日报补采或对账失败，本轮影子判断已阻断，不使用旧日报。请检查本机日报状态记录。'
    FeishuAppNotifier(env['FEISHU_APP_ID'],env['FEISHU_APP_SECRET'],chat).send_card(card('千川日报｜需要核验',[text_element('数据日期：'+status['target_day']),text_element(text),text_element('计划暂停保持关闭。') ]),request_uuid=identity)
print(deliver(home,status,send))
