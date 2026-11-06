import unittest,tempfile,json,sqlite3,sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone
from agent import listener

class KB:
    last_context=None
    report_snapshot=None
    def ask(self,q,env,report_context=None):
        self.last_context=report_context
        return report_context

class ProfitAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'config').mkdir();(self.root/'out').mkdir()
        (self.root/'config/daily_brief.json').write_text('{"profit_report":true}')
        self.env={'QC_ADVERTISER_IDS':'123','KNOWLEDGE_ENABLED':'true'}
        clock=patch('agent.profit_analysis.datetime',wraps=datetime)
        clock.start().now.return_value=datetime(2026,11,6,2,tzinfo=timezone.utc)
        self.addCleanup(clock.stop)
        old={'source_api':{'advertiser_id':'123'},'audit':{'start':'2026-11-04','end':'2026-11-04','days':1},'plans':[],'totals':{}}
        (self.root/'out/model_old.json').write_text(json.dumps(old))
    def save(self,day='2026-11-05',account='123',valid=True):
        d=self.root/'out/profit_daily'/account/day/'100000';d.mkdir(parents=True,exist_ok=True)
        obj={'version':'qianchuan-plan-scenario-v1','account':account,'date':day,'rows':[{'plan_id':'1','name':'商品','profit':None,'status':'待核验'}],'calculable':0,'missing_profit':1,'totals_calculable_only':{'profit':'0.00'},'checks':[],'warnings':['缺数据'],'policy':{},'as_of':'2026-11-06T10:00:00+08:00'}
        (d/'result.json').write_text(json.dumps(obj) if valid else '{')
        p=self.root/'data/qianchuan';p.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(p/'daily_brief.db') as c:
            c.execute('create table if not exists jobs(account text,day text,sent integer,payload text)')
            c.execute('insert into jobs values(?,?,?,?)',(account,day,1,json.dumps({'directory':str(d),'date':day})))
    def answer(self,q):
        return listener.answer(q,self.root/'out',self.env,knowledge=KB())
    def test_yesterday_uses_new_report_not_old_model(self):
        self.save();s=self.answer('昨日计划分析');self.assertIn('2026-11-05',s);self.assertIn('待核验',s);self.assertNotIn('2026-11-04',s)
    def test_missing_yesterday_does_not_fallback(self):
        s=self.answer('昨日计划分析');self.assertIn('2026-11-05',s);self.assertIn('待核验',s);self.assertNotIn('数据窗口：2026-11-04',s)
    def test_explicit_date_not_replaced_by_latest(self):
        self.save();s=self.answer('分析2026-11-03日报');self.assertIn('2026-11-03',s);self.assertIn('待核验',s);self.assertNotIn('商品',s)
    def test_corrupt_report_fails_closed(self):
        self.save(valid=False);self.assertIn('待核验',self.answer('昨日计划分析'))
    def test_other_account_not_used(self):
        self.save(account='999');self.assertIn('待核验',self.answer('昨日计划分析'))



    def test_reply_discards_legacy_followup_source(self):
        from agent.conversation import Conversation
        self.save();memory=Conversation(self.root/'conversation.db',account='123')
        memory.save('chat','user',[{'question':'昨日计划分析','answer':'旧数据', 'source':'旧11月4日价格档缺失'}])
        class Notifier:
            def reply_card(self,mid,card):return card
        with patch('agent.listener.Knowledge',KB):
            card=listener.reply_command(Notifier(),'msg','再简单点',self.root/'out',self.env,
                {'chat_id':'chat','sender_id':'user'},memory)
        text=json.dumps(card,ensure_ascii=False)
        self.assertIn('2026-11-05',text);self.assertNotIn('旧11月4日',text)
        self.assertIn('新版利润日报',memory.load('chat','user')[-1]['source'])
    def test_missing_date_followup_does_not_return_to_previous_report(self):
        from agent.conversation import Conversation
        self.save();memory=Conversation(self.root/'conversation.db',account='123')
        class Notifier:
            def reply_card(self,mid,card):return card
        with patch('agent.listener.Knowledge',KB):
            for q in ['昨日计划分析','分析2026-11-03日报','再简单点']:
                card=listener.reply_command(Notifier(),'msg',q,self.root/'out',self.env,
                    {'chat_id':'chat','sender_id':'user'},memory)
        text=json.dumps(card,ensure_ascii=False)
        self.assertIn('2026-11-03',text);self.assertIn('待核验',text)
        self.assertNotIn('2026-11-05',text)

    def test_explicit_yesterday_not_overridden_by_current_word(self):
        self.save()
        self.assertIn('2026-11-05',self.answer('现在分析昨日日报'))

class DateAndConversationTests(unittest.TestCase):
    def test_utc_evening_is_next_day_in_beijing(self):
        from agent.profit_analysis import target_day
        self.assertEqual(target_day('昨日分析',datetime(2026,11,5,16,1,tzinfo=timezone.utc)),'2026-11-05')
        self.assertEqual(target_day('昨日分析',datetime(2026,11,5,15,59,tzinfo=timezone.utc)),'2026-11-04')
    def test_year_boundary_and_short_dates(self):
        from agent.profit_analysis import target_day
        n=datetime(2026,12,31,17,tzinfo=timezone.utc)
        self.assertEqual(target_day('昨天',n),'2026-12-31')
        self.assertEqual(target_day('分析2026年11月5日日报',n),'2026-11-05')
        self.assertEqual(target_day('分析11.5日报',datetime(2026,11,6,tzinfo=timezone.utc)),'2026-11-05')
    def test_conflicting_dates_are_not_silently_selected(self):
        from agent.profit_analysis import target_day
        n=datetime(2026,11,6,tzinfo=timezone.utc)
        for q in ['昨日11-4日报','分析11-4至11-5日报','分析11-31日报','分析2026-11-06日报']:
            with self.subTest(q=q),self.assertRaises(ValueError):target_day(q,n)
    def test_old_conversation_not_reused(self):
        from agent.profit_analysis import followup_command
        old={'source':'{"数据窗口":"2026-11-04"}'}
        self.assertEqual(followup_command('再简单点',old),'再简单点')
        new={'source':'{"资料类型":"新版利润日报","数据日期":"2026-11-03"}'}
        self.assertIn('2026-11-03',followup_command('再简单点',new))
        self.assertEqual(followup_command('昨日再简单点',new),'昨日再简单点')

class PromptTests(unittest.TestCase):
    def test_new_report_policy_overrides_legacy_formula(self):
        from agent.knowledge import Knowledge
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"choices":[{"message":{"content":"ok"}}]}'
        with tempfile.TemporaryDirectory() as tmp,patch('urllib.request.urlopen',return_value=Response()) as request:
            Knowledge(Path(tmp)/'kb.db').ask('分析日报',{'DEEPSEEK_API_KEY':'test','DEEPSEEK_MODEL':'test'},
                report_context=json.dumps({'资料类型':'新版利润日报','数据日期':'2026-11-05','报告':{'policy':{'fee_rate':'.1310'}}}))
            system=json.loads(request.call_args.args[0].data)['messages'][0]['content']
            self.assertIn('以新版利润日报的policy与已计算金额为准',system)

if __name__=='__main__':unittest.main()
