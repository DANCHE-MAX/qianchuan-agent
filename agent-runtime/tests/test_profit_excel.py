"""Optional real renderer regression, exclusively synthetic money/identity fixtures.
Set QC_TEST_NODE to an installed Node executable and install @oai/artifact-tool
in tools/node_modules. No production config, reports, or credentials are read.
"""
import json,os,subprocess,tempfile,unittest,zipfile
from pathlib import Path
from decimal import Decimal
from xml.etree import ElementTree as ET
from agent.plan_profit import calculate
ROOT=Path(__file__).resolve().parents[1]

class ExcelTests(unittest.TestCase):
 @unittest.skipUnless(os.environ.get('QC_TEST_NODE') and (ROOT/'tools/node_modules/@oai/artifact-tool').exists(),'Requires explicitly configured Node and artifact-tool')
 def test_half_cent_costs_match_decimal_in_exported_workbook(self):
  policy={'refund_rate':'0','purchase_multiplier':'3.2','fee_rate':'.13','ad_rate':'.15','shipping_mode':'excluded'}
  rows=[]
  for i,amount in enumerate(['7.76','18.32','25.04','41.68']):
   row={'plan_id':str(100+i),'name':'Synthetic item '+str(i),'status':'synthetic','gmv':amount,'spend':'1.23'}
   row.update(calculate(amount,'1.23',policy));rows.append(row)
  data={'date':'2026-01-01','rows':rows,'calculable':len(rows),'missing_profit':0,'policy':policy,'policy_version':'synthetic','checks':[],'warnings':[],
        'totals_calculable_only':{key:str(sum(Decimal(r[field]) for r in rows)) for key,field in [('profit','profit'),('gmv','gmv'),('spend','spend')]}}
  with tempfile.TemporaryDirectory() as temp:
   source=Path(temp)/'result.json';source.write_text(json.dumps(data))
   result=subprocess.run([os.environ['QC_TEST_NODE'],str(ROOT/'tools/profit_assets.mjs'),str(source)],capture_output=True,text=True,timeout=120)
   self.assertEqual(result.returncode,0,result.stderr[-2000:])
   with zipfile.ZipFile(Path(temp)/'profit-2026-01-01.xlsx') as archive:
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    sheet=ET.fromstring(archive.read('xl/worksheets/sheet2.xml'))
    cells={c.attrib['r']:c.findtext('s:v',namespaces=ns) for c in sheet.findall('.//s:c',ns)}
    for i,row in enumerate(rows,7):
     self.assertEqual(Decimal(cells['G'+str(i)]).quantize(Decimal('.01')),Decimal(row['cost']))
     self.assertEqual(Decimal(cells['J'+str(i)]).quantize(Decimal('.01')),Decimal(row['profit']))

if __name__=='__main__':unittest.main()
