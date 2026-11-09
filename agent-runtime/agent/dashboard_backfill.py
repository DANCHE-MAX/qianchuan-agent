"""Read-only local historical recollections, never evidence of past delivery."""
import hashlib,json
from pathlib import Path

def load(root,account,day):
 root=Path(root).resolve();index=root/'data/dashboard_backfills'/str(account)/(day+'.json')
 if not index.exists():return None
 value=json.loads(index.read_text())
 if str(value.get('account'))!=str(account) or value.get('date')!=day or value.get('status')!='collected_unsent' or value.get('scope')!='product_only':raise ValueError('Backfill scope mismatch')
 directory=(root/value['directory']).resolve()
 if not directory.is_relative_to(root):raise ValueError('Backfill directory outside root')
 result=directory/'result.json'
 if str(result.relative_to(root)) not in value['files']:raise ValueError('Unbound backfill report')
 for name,digest in value['files'].items():
  path=(root/name).resolve()
  if not path.is_relative_to(directory) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Backfill evidence changed')
 report=json.loads(result.read_text())
 if str(report.get('account'))!=str(account) or report.get('date')!=day:raise ValueError('Backfill result scope mismatch')
 if any(r.get('goal')=='LIVE_PROM_GOODS' for r in report['rows']):raise ValueError('Live row outside scope')
 report['_backfill_status']='历史补采测算·未发送'
 report['_backfill_note']='本次补采商品计划，不含直播；按本次确认口径估算，不是当时已发日报或实际结算利润。'
 return report
