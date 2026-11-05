"""Rebuild local history; API data stays in private evidence, no live writes."""
import argparse,json,hashlib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from qc_shadow.history_build import derive
p=argparse.ArgumentParser();p.add_argument('--output',default='normalized-history-v3.json');args=p.parse_args()
folder=Path('evidence/source-v2/private')
files={path.name:path.read_bytes() for path in folder.glob('*.json') if path.name.startswith('logs-') or
       (path.name.startswith('daily-') and path.name.endswith(('-plan.json','-day.json'))) or path.name=='overlap-check.json'}
h=derive(files);h.update(version='source-history-v3',built_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),execution_enabled=False,
 note='完整日是读取时的日窗口完整与同范围对账通过，不是最终结算；缺行未知，修订保留版本。')
raw=Path('/Users/example/Library/Application Support/CANOTWAIT/QianchuanAgent/config/rules.json').read_bytes();cfg=json.loads(raw)['model'];sha=hashlib.sha256(raw).hexdigest();verified={}
for pid,record in cfg.get('plans',{}).items():
 if record.get('basis')!='用户指定：托管采用季节整体退款率，不拆商品结构':continue
 band=record['refund_band'];rate=cfg['refund_rates'][cfg['season']][band]
 verified[pid]={'band':band,'rate':str(rate),'model_version':cfg['version'],'season':cfg['season'],
  'source_config_sha256':sha,'evidence':'deployed_config_explicit_user_mapping:'+pid,'basis':record['basis']}
h['verified_refund_mappings']=verified
out=(folder/args.output).resolve()
if out.parent!=folder.resolve():raise ValueError('output_must_be_private_basename')
with out.open('x') as f:json.dump(h,f,ensure_ascii=False,indent=2)
out.chmod(0o600)
print(json.dumps({'first_enabled':sum(v['verified'] for v in h['first_enabled'].values()),'plan_days':len(h['daily']),
'admitted':sum(r['complete'] for r in h['daily']),'reconciled_days':sum(h['daily_reconciliation'].values()),'revisions':len(h['revisions'])}))
