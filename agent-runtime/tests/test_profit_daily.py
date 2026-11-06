import hashlib,json,tempfile,unittest
from pathlib import Path
from datetime import date,datetime
from unittest.mock import Mock,patch
from agent import profit_daily as p,profit_delivery as delivery,daily_brief,hourly

class CollectionTests(unittest.TestCase):
 def test_empty_report_is_not_zero_and_only_video_zero_evidence(self):
  calls=[]
  def request(env,path,args):
   calls.append((path,args))
   if path==p.LIST:
    rows=[]
    if args['adlab_scene']=='OVERALL_PROJECT':
     pid='101' if args['marketing_goal']=='VIDEO_PROM_GOODS' else '102'
     rows=[{'ad_info':{'id':pid,'name':'synthetic'},'product_info':[]}]
    return {'code':0,'request_id':'synthetic','data':{'ad_list':rows,'page_info':{'page':1,'total_page':1 if rows else 0,'total_num':len(rows)}}}
   return {'code':0,'data':{'rows':[],'page_info':{'page':1,'total_page':0,'total_number':0}}}
  with tempfile.TemporaryDirectory() as d:
   r=p.collect({'QC_ADVERTISER_IDS':'123'},'2026-01-01',date(2026,1,2),Path(d),request)
  self.assertEqual(len(r['inventory']),2);self.assertEqual(len(r['no_spend']),1)
  self.assertEqual(r['daily'],[]);self.assertTrue(all(c['difference'] is None for c in r['checks']))
  self.assertTrue(all(path in (p.LIST,p.REPORT) for path,args in calls))

 def test_pagination_incomplete_fails(self):
  request=lambda *args:{'code':0,'data':{'rows':[{}],'page_info':{'page':1,'total_page':1,'total_number':2}}}
  with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):p.pages({},p.REPORT,{},Path(d),'demo',request)

 def test_today_rejected_before_api(self):
  req=Mock()
  with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):p.collect({},'2026-01-02',date(2026,1,2),Path(d),req)
  req.assert_not_called()

class DeliveryTests(unittest.TestCase):
 def test_successful_part_is_not_resent_after_readback_failure(self):
  with tempfile.TemporaryDirectory() as d:
   bundle={'directory':d,'date':'2026-01-01','actors':['ou_demo'],'text':'2026-01-01','files':[]}
   api=Mock();api.chat_id='chat';api._send.return_value={'data':{'message_id':'message'}}
   message={'chat_id':'chat','msg_type':'text','body':{'content':json.dumps({'text':'2026-01-01'})},'mentions':[{'id':'ou_demo'}]}
   api.get_message.side_effect=[RuntimeError('temporary'),[message]]
   with self.assertRaises(RuntimeError):delivery.deliver(bundle,api,'uuid')
   delivery.deliver(bundle,api,'uuid');delivery.deliver(bundle,api,'uuid')
   self.assertEqual(api._send.call_count,1)

 def test_unknown_send_outcome_blocks_resend(self):
  with tempfile.TemporaryDirectory() as d:
   bundle={'directory':d,'date':'2026-01-01','actors':['ou_demo'],'text':'2026-01-01','files':[]}
   api=Mock();api.chat_id='chat';api._send.side_effect=TimeoutError()
   with self.assertRaises(TimeoutError):delivery.deliver(bundle,api,'uuid')
   with self.assertRaises(delivery.AmbiguousDelivery):delivery.deliver(bundle,api,'uuid')
   self.assertEqual(api._send.call_count,1)

 def test_missing_mention_is_not_verified(self):
  with tempfile.TemporaryDirectory() as d:
   bundle={'directory':d,'date':'2026-01-01','actors':['ou_demo'],'text':'2026-01-01','files':[]}
   api=Mock();api.chat_id='chat';api._send.return_value={'data':{'message_id':'message'}}
   api.get_message.return_value=[{'chat_id':'chat','msg_type':'text','body':{'content':'{"text":"2026-01-01"}'},'mentions':[]}]
   with self.assertRaises(RuntimeError):delivery.deliver(bundle,api,'uuid')
   self.assertNotIn('complete',json.loads((Path(d)/'delivery.json').read_text()))

class ScheduleTests(unittest.TestCase):
 def test_daily_existing_ledger_completes_only_after_bundle_verified(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'config').mkdir();(root/'config/daily_brief.json').write_text('{"profit_report":true}')
   env={'QC_ADVERTISER_IDS':'123','FEISHU_CHAT_ID':'chat'}
   engine=daily_brief.Engine(root,env,Mock());now=datetime(2026,1,2,10,0,tzinfo=hourly.CHINA)
   with patch('agent.daily_brief.settings.load_config',return_value={}),patch('agent.daily_brief.prepare',return_value={'type':'profit_bundle'}) as prepare,patch('agent.profit_delivery.deliver',side_effect=[RuntimeError('retry'),{}]) as send:
    self.assertFalse(engine.tick(now));self.assertTrue(engine.tick(now.replace(minute=2)));self.assertFalse(engine.tick(now.replace(minute=3)))
    self.assertEqual(prepare.call_count,1);self.assertEqual(send.call_count,2)
    restarted=daily_brief.Engine(root,env,Mock());self.assertFalse(restarted.tick(now.replace(minute=4)))

 def test_scheduler_routes_profit_mode_and_keeps_old_default(self):
  with tempfile.TemporaryDirectory() as d,patch('agent.profit_daily.prepare',return_value={'type':'profit_bundle'}) as prepare,patch('agent.two_day_daily.attach',side_effect=lambda root,env,now,bundle:bundle),patch('agent.daily_guide.attach',side_effect=lambda root,env,now,bundle:bundle),patch('agent.report_preflight.seal',side_effect=lambda bundle:bundle):
   self.assertEqual(daily_brief.prepare(d,{},None,{}, {'profit_report':True}),{'type':'profit_bundle'})
   self.assertFalse(daily_brief.options(d).get('profit_report',False))

class RateLimitTests(unittest.TestCase):
 def test_raised_rate_limit_recovers_with_bounded_delay(self):
  from agent.reliability import ServiceError
  for code in (40100,429):
   with self.subTest(code=code),patch.object(p.qc_auth,'call',side_effect=[ServiceError('synthetic',api_code=code),{'code':0,'data':{}}]) as call,patch.object(p.time,'sleep') as sleep:
    self.assertEqual(p.get({},p.REPORT,{}),{'code':0,'data':{}})
    self.assertEqual(call.call_count,2);sleep.assert_called_once_with(6)
 def test_rate_limit_exhaustion_stops_after_three_attempts(self):
  from agent.reliability import ServiceError
  with patch.object(p.qc_auth,'call',side_effect=ServiceError('synthetic',api_code=40100)) as call,patch.object(p.time,'sleep') as sleep:
   with self.assertRaises(ServiceError):p.get({},p.REPORT,{})
   self.assertEqual(call.call_count,3)
   self.assertEqual([c.args[0] for c in sleep.call_args_list],[6,12])
 def test_non_rate_errors_are_not_retried(self):
  from agent.reliability import ServiceError
  with patch.object(p.qc_auth,'call',side_effect=ServiceError('synthetic',api_code=40002)) as call,patch.object(p.time,'sleep') as sleep:
   with self.assertRaises(ServiceError):p.get({},p.REPORT,{})
   self.assertEqual(call.call_count,1);sleep.assert_not_called()

if __name__=='__main__':unittest.main()
