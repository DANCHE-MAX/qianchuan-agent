"""Failure-only notification, isolated deduplication; no normal observation cards."""
import argparse,json,sys
from pathlib import Path
from qc_shadow.daily_notice import deliver
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--home',required=True);a=p.parse_args();home=Path(a.home)
s=json.loads((home/'roi/latest.json').read_text())
if s['status']=='ok':sys.exit(0)
status={'status':'failed','target_day':'roi:'+s['at'][:10],'revisions':0}
sys.path.insert(0,a.source)
from agent import settings
from agent.notify import FeishuAppNotifier,feishu_allowed_chat_ids
from agent.card_view import card,text_element
env=settings.load_env(Path(a.source)/'config/.env');chat=env.get('FEISHU_ALERT_CHAT_ID') or env.get('FEISHU_CHAT_ID')
if not chat or chat not in feishu_allowed_chat_ids(env):raise ValueError('notification_scope_invalid')
def send(identity,category,status):
 FeishuAppNotifier(env['FEISHU_APP_ID'],env['FEISHU_APP_SECRET'],chat).send_card(card('千川ROI影子｜采集需核验',[text_element('ROI只读补采或对账有%d项异常，缺失值未补零，请核本机roi/latest.json。'%len(s['errors'])),text_element('不自动修改ROI，计划暂停关闭，预算仍走既有门禁。')]),request_uuid=identity)
print(deliver(home/'roi',status,send))
