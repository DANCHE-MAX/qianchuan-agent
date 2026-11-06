"""Do not call every unmapped plan store-managed; accept verified exact-day history."""
import sys,json,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import two_day_daily as t,daily_guide as g,product_spend_daily as s
from tests.test_two_day_daily import POLICY
class Tests(unittest.TestCase):
 def test_managed_is_separate_from_unmapped(self):
  day={'day':'2026-01-01','complete':True,'warnings':[],'rows':[
   {'plan':'1','product':None,'name':'','managed':True,'gmv':'0','amount':'2','slot':0},
   {'plan':'2','product':None,'name':'','gmv':'100','amount':'1','slot':0}]}
  v=t.pie_view([day],POLICY)['days'][0]
  self.assertEqual(v.get('managed_profit'),'-2.00');self.assertEqual(v['unknown_profit'],'49.00')
  self.assertEqual(v['positive'],[])
  bars=s.build([day]);self.assertEqual(bars.get('managed'),'2');self.assertEqual(bars['unknown'],'1')
 def test_verified_same_date_report_can_be_loaded_without_new_ledger(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);out=root/'out/profit_daily/123/2026-01-01/complete';out.mkdir(parents=True)
   result={'account':'123','date':'2026-01-01','rows':[],'calculable':0,'missing_profit':0,'totals_calculable_only':{'gmv':None,'spend':None,'profit':None},'policy':POLICY}
   (out/'result.json').write_text(json.dumps(result));(out/'profit-2026-01-01-01.png').write_bytes(b'image');(out/'profit-2026-01-01.xlsx').write_bytes(b'book')
   self.assertIsNone(g.previous(root,'123','2026-01-01'))
   (out/'delivery.json').write_text(json.dumps({'complete':True,'parts':{str(i):{'verified':True,'message_id':'synthetic'} for i in range(3)}}))
   self.assertIsNotNone(g.previous(root,'123','2026-01-01'))
   self.assertIsNone(g.previous(root,'123','2026-01-02'))
if __name__=='__main__':unittest.main()
