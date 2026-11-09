"""Failure-only notification, isolated deduplication; no normal observation cards."""
import argparse,json,sys
from pathlib import Path
from qc_shadow.daily_notice import deliver
from qc_shadow.roi_notice_text import lines,previous_names
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
 FeishuAppNotifier(env['FEISHU_APP_ID'],env['FEISHU_APP_SECRET'],chat).send_card(card('千川ROI影子｜采集需核验',[text_element(line) for line in lines(s,previous_names(home/'roi',s.get('plans',[])))]),request_uuid=identity)
print(deliver(home/'roi',status,send))
