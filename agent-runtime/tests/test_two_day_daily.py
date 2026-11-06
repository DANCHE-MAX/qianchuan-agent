"""Synthetic two-day daily bundle, profitable-only shares and durable dates."""
from pathlib import Path
import sys,json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import two_day_daily as t,product_spend_daily as spend,daily_brief
POLICY={'refund_rate':0,'purchase_multiplier':2,'fee_rate':0,'ad_rate':.15,'shipping_mode':'excluded'}
def row(plan,product,gmv,cost):return {'plan':plan,'product':product,'name':'商品'+str(product),'slot':0,'amount':cost,'gmv':gmv}
def data(day):return {'day':day,'complete':True,'rows':[row('1','a','100','10'),row('2','a','0','5'),row('3','b','0','20'),row('4',None,'100','5')],'warnings':[],'inventory':['1','2','3','4','5'],'inventory_complete':True}
class Tests(unittest.TestCase):
 def test_group_before_profit_loss_classification(self):
  v=t.pie_view([data('2026-01-01'),data('2026-01-02')],POLICY)
  d=v['days'][0]
  self.assertEqual(d['positive_total'],'35.00');self.assertEqual(d['loss_total'],'-20.00')
  self.assertEqual(d['unknown_profit'],'45.00');self.assertEqual(d['missing_plans'],1)
  self.assertEqual(len(d['positive']),1);self.assertEqual(d['positive'][0]['share'],'100')
  self.assertEqual(d['ad_total'],'40');self.assertEqual(sum(float(g['share']) for g in d['ads']),100)
 def test_failed_day_and_zero_baselines_not_filled(self):
  d=data('2026-01-01');d['complete']=False
  v=t.pie_view([d],POLICY)['days'][0];self.assertIsNone(v['positive_total']);self.assertIsNone(v['ad_total'])
  d=data('2026-01-02');d['rows']=[row('1','a','0','0')]
  v=t.pie_view([d],POLICY)['days'][0];self.assertEqual(v['positive'],[]);self.assertEqual(v['positive_total'],'0')
 def test_mismatch_with_main_report_marks_day_unknown(self):
  d=data('2026-01-02');t.reconcile_main(d,{'rows':[{'plan_id':'1','gmv':'100','spend':'999'}]})
  self.assertFalse(d['complete']);self.assertIn('日报',str(d['warnings']))
 def test_daily_prepare_attaches_only_for_scheduled_new_bundle(self):
  with tempfile.TemporaryDirectory() as root,patch('agent.profit_daily.prepare',return_value={'type':'profit_bundle'}) as prep,patch.object(t,'attach',return_value={'type':'profit_bundle','extra':True}) as attach,patch('agent.daily_guide.attach',side_effect=lambda root,env,now,bundle:bundle):
   v=daily_brief.prepare(root,{},datetime(2026,1,3,10,tzinfo=timezone(timedelta(hours=8))),{}, {'profit_report':True})
   self.assertTrue(v['extra']);attach.assert_called_once()
 def test_midnight_sender_retired(self):
  with patch.object(spend,'prepare') as prepare:
   self.assertFalse(spend.tick(Mock(),datetime(2026,1,3,0,tzinfo=timezone.utc)));prepare.assert_not_called()
 def test_attach_pins_two_completed_days_and_preserves_original_files(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'config').mkdir();(root/'config/profit_daily.json').write_text('{"render_python":"synthetic-python","font":"synthetic-font"}')
   directory=root/'out';directory.mkdir()
   main={'date':'2026-01-02','account':'123','policy':POLICY,'rows':[{'plan_id':r['plan'],'gmv':r['gmv'],'spend':r['amount']} for r in data('2026-01-02')['rows']]}
   (directory/'result.json').write_text(json.dumps(main))
   originals=[{'kind':'image','path':'old.png','sha256':'old'},{'kind':'file','path':'old.xlsx','sha256':'book'}]
   bundle={'directory':str(directory),'date':'2026-01-02','account':'123','files':originals,'text':'original','actors':['ou_synthetic']}
   def render(args,**kwargs):Path(args[3]).write_bytes(b'synthetic image')
   with patch.object(spend,'collect',return_value=[data('2026-01-01'),data('2026-01-02')]) as collect,patch.object(t.subprocess,'run',side_effect=render):
    result=t.attach(root,{'QC_ADVERTISER_IDS':'123'},datetime(2026,1,3,2,tzinfo=timezone.utc),bundle)
   self.assertEqual(collect.call_args.args[1],['2026-01-01','2026-01-02'])
   self.assertEqual(result['files'][:2],originals);self.assertEqual(len(result['files']),4)
   self.assertEqual(len(bundle['files']),2);self.assertEqual(result['actors'],['ou_synthetic'])
   self.assertEqual(result['comparison_days'],['2026-01-01','2026-01-02'])
 def test_unknown_does_not_enter_profit_pie_and_shares_use_product_net(self):
  d=data('2026-01-01');d['rows'].append(row('6','a','0','100'))
  v=t.pie_view([d],POLICY)['days'][0]
  self.assertEqual(v['positive'],[]);self.assertEqual(v['loss_total'],'-85.00')
  self.assertEqual(v['unknown_profit'],'45.00')
 def test_combined_bundle_delivery_verifies_mentions_and_never_repeats_parts(self):
  from agent import profit_delivery
  import hashlib
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);files=[]
   for name,kind in [('guide.png','image'),('main.png','image'),('main.xlsx','file'),('stack.png','image'),('pies.png','image')]:
    path=root/name;path.write_bytes(name.encode());files.append({'kind':kind,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
   bundle={'directory':tmp,'date':'2026-01-02','text':'2026-01-02 daily','actors':['ou_synthetic'],'files':files}
   api=Mock();api.chat_id='oc_synthetic';api.BASE='https://example.invalid';api._tenant_token.return_value='synthetic';messages={}
   def send(kind,content,request_uuid=None):
    mid='m'+str(len(messages));body=dict(content)
    if kind=='file':body['file_name']='main.xlsx'
    messages[mid]={'chat_id':api.chat_id,'msg_type':kind,'body':{'content':json.dumps(body)},'mentions':[{'id':'ou_synthetic'}]}
    return {'data':{'message_id':mid}}
   api._send.side_effect=send;api.get_message.side_effect=lambda mid:[messages[mid]]
   upload=lambda api,kind,path:path.name
   with patch.object(profit_delivery.requests,'get',return_value=Mock(status_code=200,content=b'main.xlsx')):
    self.assertTrue(profit_delivery.deliver(bundle,api,'fixed',upload_fn=upload)['complete'])
    self.assertTrue(profit_delivery.deliver(bundle,api,'fixed',upload_fn=upload)['complete'])
   self.assertEqual(api._send.call_count,6)
   self.assertEqual(sum('<at user_id=' in json.dumps(c.args[1]) for c in api._send.call_args_list),1)
   self.assertEqual(api._send.call_args_list[1].args[1]['image_key'],'guide.png')
   self.assertEqual(len({c.kwargs['request_uuid'] for c in api._send.call_args_list}),6)
if __name__=='__main__':unittest.main()
