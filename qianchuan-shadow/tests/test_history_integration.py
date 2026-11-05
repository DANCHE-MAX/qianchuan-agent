import json,unittest,tempfile,hashlib
from pathlib import Path
from qc_shadow.adapter import Source
from qc_shadow.daily_rules import assess_daily
import test_data
class IntegrationTests(unittest.TestCase):
 setUp=test_data.DataTests.setUp
 def bundle(self):
  from test_history import logbundle,dailybundle
  from qc_shadow.history_build import derive
  files={'logs-1.json':json.dumps(logbundle()).encode(),'daily-2026-11-01-plan.json':json.dumps(dailybundle()).encode(),
    'daily-2026-11-01-day.json':json.dumps(dailybundle(granularity='day')).encode(),'overlap-check.json':json.dumps(dailybundle()).encode()}
  for name,raw in files.items():(self.root/name).write_bytes(raw)
  h=derive(files);h.update(version='source-history-v3',built_at='2026-11-02T10:00:00+08:00')
  p=self.root/'history.json';p.write_text(json.dumps(h));return p
 def test_enriches_without_zero_fill(self):
  p=self.bundle();s=Source(self.root,history_path=p).snapshot('9','2026-11-02T10:00:00+08:00');plan=s['plans'][0]
  self.assertEqual(plan['first_enabled_on'],'2026-10-01');self.assertEqual(len(plan['daily']),1);self.assertEqual(plan['daily'][0]['orders'],0)
 def test_source_tamper_rejected(self):
  p=self.bundle();(self.root/'logs-1.json').write_text('changed')
  with self.assertRaises(ValueError):Source(self.root,history_path=p).snapshot('9','2026-11-02T10:00:00+08:00')
class DailyRulesTests(unittest.TestCase):
 def rows(self):return [{'day':'2026-10-%02d'%i,'spend':'10','gmv':'100','orders':5 if i==30 else 10,'complete':True,'evidence':'fixture'} for i in range(25,32)]
 def refund(self):return {'rate':'0','band':'fixture','model_version':'v1','verified':True}
 def test_recent_quality_independent_of_lifetime_and_clock(self):
  r=assess_daily(self.rows(),'2026-11-01T10:00:00+08:00',self.refund());self.assertEqual(r['Q-GROW'],'met');self.assertEqual(r['Q-STABLE'],'met');self.assertFalse(r['execution_allowed'])
 def test_unknown_refund_not_profit(self):
  f=self.refund();f['verified']=False;r=assess_daily(self.rows(),'2026-11-01T10:00:00+08:00',f);self.assertEqual(r['Q-GROW'],'unknown')
 def test_missing_day_unknown(self):
  r=assess_daily(self.rows()[:-1],'2026-11-01T10:00:00+08:00',self.refund());self.assertEqual(r['Q-STABLE'],'unknown')
 def test_break_even_is_not_profitable(self):
  rows=self.rows();rows[-1].update(spend='55.66');r=assess_daily(rows,'2026-11-01T10:00:00+08:00',self.refund());self.assertEqual(r['Q-GROW'],'not_met')
