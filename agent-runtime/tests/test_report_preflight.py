"""Synthetic-only send barrier: no uploads/messages on conflicts; missing stays unknown."""
import sys,json,tempfile,unittest,importlib.util,zipfile,hashlib,base64
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import plan_profit as p,daily_guide as g,two_day_daily as t,product_spend_daily as s,profit_delivery
from xml.etree.ElementTree import Element,SubElement,tostring
POLICY={'refund_rate':'0','purchase_multiplier':'2','fee_rate':'0','ad_rate':'.15','shipping_mode':'excluded'}
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1sAAAAASUVORK5CYII=')

def book(path,main):
 ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
 sheets=[]
 detail={'A2':'2026-01-01 全部计划测算'}
 for i,r in enumerate(main['rows'],7):
  detail.update({f'A{i}':r['name'],f'B{i}':r['plan_id']})
  for col,key in [('D','gmv'),('E','spend'),('F','net'),('G','cost'),('H','fee'),('I','preprofit'),('J','profit'),('K','ad15'),('L','profit15'),('M','headroom'),('O','candidate')]:detail[col+str(i)]='待核验' if r[key] is None else float(r[key])
  detail['P'+str(i)]='待核验'
 summary={'A2':'2026-01-01 昨日计划预估净利润'}
 known=[r for r in main['rows'] if r['profit'] is not None]
 for i,r in enumerate(known,7):summary.update({f'A{i}':r['name'],f'B{i}':float(r['spend']),f'C{i}':float(r['profit']),f'D{i}':float(r['gmv'])})
 for col,key in [('B','spend'),('C','profit'),('D','gmv')]:summary[col+str(7+len(known))]=float(main['totals_calculable_only'][key])
 policy={'B6':0.,'B7':2.,'B8':0.,'B9':.15,'A12':'数据日期：2026-01-01','A13':'账户：123'}
 with zipfile.ZipFile(path,'w') as z:
  names=['昨日计划汇总','全部计划测算','模型口径与覆盖','缺失与对账']
  wb=Element('workbook',xmlns=ns);ss=SubElement(wb,'sheets');rels=Element('Relationships',xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
  for n,(name,cells) in enumerate(zip(names,[summary,detail,policy,{}]),1):
   SubElement(ss,'sheet',name=name,sheetId=str(n),attrib={'{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id':'rId'+str(n)})
   SubElement(rels,'Relationship',Id='rId'+str(n),Target='worksheets/sheet'+str(n)+'.xml',Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet')
   sh=Element('worksheet',xmlns=ns);data=SubElement(sh,'sheetData')
   for ref,v in cells.items():
    row=SubElement(data,'row');c=SubElement(row,'c',r=ref,t='n' if isinstance(v,float) else 'str');SubElement(c,'v').text=str(v)
   z.writestr('xl/worksheets/sheet'+str(n)+'.xml',tostring(sh))
  z.writestr('xl/workbook.xml',tostring(wb));z.writestr('xl/_rels/workbook.xml.rels',tostring(rels))

def fixture(root):
 rows=[]
 for pid,name,gmv,spend in [('1','商品A','100','10'),('2','全店托管','0','2'),('3','待核验商品',None,'0')]:
  rows.append(dict(plan_id=pid,name=name,gmv=gmv,spend=spend,**p.calculate(gmv,spend,POLICY)))
 main={'date':'2026-01-01','account':'123','policy':POLICY,'rows':rows,'calculable':2,'missing_profit':1,'totals_calculable_only':{'gmv':'100','spend':'12','profit':'38'},'checks':[],'warnings':[],'execution_enabled':False,'feedback_required':False}
 days=[{'day':'2025-12-31','account':'123','complete':False,'rows':[],'warnings':['历史缺失'],'conflicts':[]}, {'day':'2026-01-01','account':'123','complete':True,'rows':[{'plan':'1','product':'a','name':'商品A','gmv':'100','amount':'10','slot':0,'managed':False},{'plan':'2','product':None,'name':'','gmv':'0','amount':'2','slot':0,'managed':True}],'warnings':[],'conflicts':[],'inventory':['1','2','3'],'inventory_complete':True,'report_missing':1}]
 inputs={'result.json':main,'guide/previous.json':None,'guide/guide.json':g.build(main,None,'2026-01-02'),'two_day/collected.json':days,'two_day/reports.json':[None,main],'two_day/stack.json':s.build(days),'two_day/pies.json':t.pie_view(days,POLICY)}
 for name,value in inputs.items():
  path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False))
 files=[]
 for name in ['guide/guide.png','profit-2026-01-01-01.png','profit-2026-01-01.xlsx','two_day/stack.png','two_day/pies.png']:
  path=root/name
  if name.endswith('.xlsx'):book(path,main)
  else:path.write_bytes(PNG)
  files.append({'kind':'file' if name.endswith('.xlsx') else 'image','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 return {'type':'profit_bundle','directory':str(root),'date':main['date'],'account':'123','actors':['ou_synthetic'],'text':'2026-01-01 简报','files':files,'guide_version':1,'comparison_days':['2025-12-31','2026-01-01']}

class Tests(unittest.TestCase):
 def module(self):
  self.assertIsNotNone(importlib.util.find_spec('agent.report_preflight'),'Pre-send gate is missing')
  from agent import report_preflight
  return report_preflight
 def test_missing_is_warning_not_zero_or_block(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as tmp:
   b=m.seal(fixture(Path(tmp)));r=m.check(b);self.assertTrue(r['allowed']);self.assertTrue(r['warnings']);self.assertEqual(r['status'],'PASS_WITH_MISSING');self.assertTrue((Path(tmp)/'preflight.json').exists())
 def test_money_conflict_blocks_before_any_network(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);b=fixture(root);d=json.loads((root/'result.json').read_text());d['rows'][0]['profit']='999';(root/'result.json').write_text(json.dumps(d));b=m.seal(b)
   api=Mock();upload=Mock()
   with self.assertRaises(m.PreflightBlocked):profit_delivery.deliver(b,api,'test',upload)
   api._send.assert_not_called();upload.assert_not_called();self.assertFalse((root/'delivery.json').exists());self.assertEqual(json.loads((root/'preflight.json').read_text())['status'],'BLOCKED')
 def test_excel_mismatch_even_with_refreshed_file_hash_blocks(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);b=fixture(root);d=json.loads((root/'result.json').read_text());d['rows'][0]['profit']='999';book(root/'profit-2026-01-01.xlsx',d);b['files'][2]['sha256']=hashlib.sha256((root/'profit-2026-01-01.xlsx').read_bytes()).hexdigest()
   with self.assertRaises(m.PreflightBlocked):m.check(m.seal(b))
 def test_asset_tamper_order_duplicate_actor_and_unsealed_block(self):
  m=self.module()
  for kind in ['asset','order','actors','unsealed']:
   with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
    b=m.seal(fixture(Path(tmp)))
    if kind=='asset':Path(b['files'][-1]['path']).write_bytes(b'changed')
    if kind=='order':b['files'][0],b['files'][1]=b['files'][1],b['files'][0]
    if kind=='actors':b['actors']*=2
    if kind=='unsealed':b.pop('preflight_version')
    with self.assertRaises(m.PreflightBlocked):m.check(b)
 def test_wrong_scope_policy_and_bucket_mixing_block(self):
  m=self.module()
  for kind in ['scope','policy','mix','guide','aggregate']:
   with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);b=fixture(root)
    target='two_day/collected.json' if kind in ['scope','mix'] else 'result.json' if kind in ['policy','aggregate'] else 'guide/guide.json';p=root/target;d=json.loads(p.read_text())
    if kind=='scope':d[1]['account']='other'
    if kind=='mix':d[1]['rows'][0]['managed']=True
    if kind=='policy':d['policy']['fee_rate']='.8'
    if kind=='guide':d['current']['profit']='999'
    if kind=='aggregate':d['checks']=[{'topic':'synthetic','metric':'stat_cost','aggregate':100,'difference':'1'}]
    p.write_text(json.dumps(d))
    with self.assertRaises(m.PreflightBlocked):m.check(m.seal(b))
 def test_valid_bundle_sends_once_and_retry_rechecks_before_network(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);b=m.seal(fixture(root));api=Mock();api.chat_id='synthetic_chat';api.BASE='https://example.invalid';api._tenant_token.return_value='synthetic';messages={}
   def send(kind,content,request_uuid=None):
    mid='m'+str(len(messages));body=dict(content)
    if kind=='file':body['file_name']='profit-2026-01-01.xlsx'
    messages[mid]={'chat_id':api.chat_id,'msg_type':kind,'body':{'content':json.dumps(body)},'mentions':[{'id':'ou_synthetic'}] if kind=='text' else []}
    return {'data':{'message_id':mid}}
   api._send.side_effect=send;api.get_message.side_effect=lambda mid:[messages[mid]]
   upload=Mock(side_effect=lambda api,kind,path:path.name)
   with patch.object(profit_delivery.requests,'get',return_value=Mock(status_code=200,content=(root/'profit-2026-01-01.xlsx').read_bytes())):
    profit_delivery.deliver(b,api,'same',upload);profit_delivery.deliver(b,api,'same',upload)
   self.assertEqual(len(messages),6);self.assertEqual(sum(v['msg_type']=='text' for v in messages.values()),1)
   receipt=(root/'delivery.json').read_bytes();Path(b['files'][-1]['path']).write_bytes(b'changed');upload.reset_mock()
   with self.assertRaises(m.PreflightBlocked):profit_delivery.deliver(b,api,'same',upload)
   self.assertEqual(len(messages),6);upload.assert_not_called();self.assertEqual((root/'delivery.json').read_bytes(),receipt)
 def test_blocked_scheduler_does_not_send_extra_failure_card(self):
  m=self.module()
  from agent import daily_brief,hourly
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'config').mkdir();(root/'config/daily_brief.json').write_text('{"profit_report":true}')
   b=m.seal(fixture(root));b['account']='wrong';api=Mock();engine=daily_brief.Engine(root,{'QC_ADVERTISER_IDS':'123','FEISHU_CHAT_ID':'chat'},lambda chat:api)
   with patch.object(daily_brief,'prepare',return_value=b),patch.object(daily_brief.settings,'load_config',return_value={}):
    for minute in (0,2,4,6):self.assertFalse(engine.tick(datetime(2026,1,2,10,minute,tzinfo=hourly.CHINA)))
   api._send.assert_not_called();api.send_card.assert_not_called();self.assertTrue((root/'preflight.json').exists())
if __name__=='__main__':unittest.main()
