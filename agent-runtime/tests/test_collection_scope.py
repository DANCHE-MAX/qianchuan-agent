import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import settings,collection_rates,hourly
from datetime import datetime
class ScopeTests(unittest.TestCase):
 def test_account_specific(self):
  cfg={'monitoring':{'account_kinds':{'999':['plan']}}}
  self.assertEqual(settings.collection_kinds(cfg,'999'),('plan',))
  self.assertEqual(settings.collection_kinds(cfg,'123'),('plan','material'))
 def test_invalid_kind_rejected(self):
  with self.assertRaises(ValueError):settings.collection_kinds({'monitoring':{'account_kinds':{'999':['other']}}},'999')
 def test_disabled_material_not_missing(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'config').mkdir();(root/'config/rules.json').write_text(json.dumps({'monitoring':{'account_kinds':{'999':['plan']}}}))
   result=collection_rates.inspect(root,{'QC_ADVERTISER_IDS':'999'},datetime(2026,11,2,18,0,tzinfo=hourly.CHINA))
   self.assertEqual(result['material']['point_state'],'not_applicable')
   self.assertEqual(result['material']['error'],'')
   self.assertNotIn('素材｜',str(collection_rates.sections(result)))
 def test_tick_fetches_plan_only(self):
  from agent import monitor as m
  from test_monitor import snap,balance,ENV,NOW
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'config').mkdir();cfg=settings.load_config();cfg['monitoring'].update(enabled=True,account_kinds={'999':['plan']})
   (root/'config/rules.json').write_text(json.dumps(cfg))
   now=NOW.replace(hour=17,minute=30)
   with patch.object(m,'fetch_snapshot',side_effect=lambda env,kind,at:snap(at,kind=kind)) as fetch,patch.object(m.account_balance,'fetch',return_value=balance(now,'5000')):
    m.Engine(root,ENV,Mock(return_value=Mock())).tick(now)
   self.assertEqual([c.args[1] for c in fetch.call_args_list],['plan'])
   self.assertEqual(m.Store(root).snapshots('999','material',now.date().isoformat()),[])
if __name__=='__main__':unittest.main()
