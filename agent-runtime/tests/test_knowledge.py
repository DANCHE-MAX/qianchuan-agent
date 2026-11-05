import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.knowledge import Knowledge
from agent.listener import answer, daily_knowledge_context

class Tests(unittest.TestCase):
    def test_ingest_search_update_and_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'rules.md'
            source.write_text('采购成本计算方式：售价除以3.2。', encoding='utf-8')
            kb = Knowledge(root / 'kb.db')
            self.assertEqual(kb.ingest(source), 1)
            self.assertTrue(kb.search('采购成本'))
            self.assertFalse(kb.search('天气预报'))
            env = {'KNOWLEDGE_ENABLED': 'true', 'DEEPSEEK_API_KEY': 'secret', 'DEEPSEEK_MODEL': 'test'}
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self): return json.dumps({'choices':[{'message':{'content':'售价除以3.2。[1]'}}]}).encode()
            with patch('urllib.request.urlopen', return_value=Response()) as request:
                result = answer('采购成本如何计算', root, env, kb)
                self.assertIn('rules.md', result)
                self.assertNotIn(str(root), result)
                self.assertEqual(request.call_count, 1)
                payload = json.loads(request.call_args.args[0].data)
                self.assertEqual(payload['thinking']['type'], 'disabled')
                self.assertEqual(payload['max_tokens'], 2000)
                self.assertIn('没有找到', answer('报告', root, env, kb))
                self.assertEqual(request.call_count, 1)
            with patch('urllib.request.urlopen', side_effect=RuntimeError('secret')):
                self.assertNotIn('secret', kb.ask('采购成本', env))
            source.write_text('广告消耗按后台导出核对。', encoding='utf-8')
            kb.ingest(source)
            self.assertFalse(kb.search('采购成本'))

    def test_no_knowledge_no_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('urllib.request.urlopen') as request:
                self.assertIn('没有找到', Knowledge(Path(tmp)/'kb.db').ask('问题', {}))
                request.assert_not_called()

    def test_daily_report_and_sop_are_both_sent_and_refresh_per_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'budget.md'
            source.write_text('预算计划须结合消耗和ROI核对。示例预算500不是实际设置。', encoding='utf-8')
            kb = Knowledge(root/'kb.db'); kb.ingest(source)
            env = {'KNOWLEDGE_ENABLED':'true', 'QC_ADVERTISER_IDS':'999',
                   'DEEPSEEK_API_KEY':'secret', 'DEEPSEEK_MODEL':'test'}
            def save(account, day, amount):
                report = {'audit':{'start':day,'end':day,'days':1}, 'season':'autumn_winter',
                          'source_api':{'advertiser_id':account}, 'totals':{'gmv':amount},
                          'plans':[{'plan_id':'123','plan_name':'马甲','configured_budget':'800'}], 'notes':[]}
                (root/('model_'+account+'_'+day+'.json')).write_text(json.dumps(report),encoding='utf-8')
            save('999','2026-10-26','100')
            save('888','2026-10-28','FOREIGN_AMOUNT')
            class Response:
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def read(self): return json.dumps({'choices':[{'message':{'content':'按日报与规则核对。[1][2]'}}]}).encode()
            with patch('urllib.request.urlopen',return_value=Response()) as request:
                result = answer('预算计划', root, env, kb)
                self.assertIn('2026-10-26',result)
                self.assertIn('[1] 本地模型日报',result)
                self.assertIn('[2] budget.md',result)
                payload = json.loads(request.call_args.args[0].data)
                evidence = json.loads(payload['messages'][1]['content'])['检索资料']
                self.assertEqual(len(evidence),2)
                self.assertIn('800',evidence[0]['内容'])
                self.assertIn('示例预算500',evidence[1]['内容'])
                self.assertNotIn('FOREIGN_AMOUNT',str(evidence))
                save('999','2026-10-27','200')
                self.assertIn('2026-10-27',answer('预算计划',root,env,kb))
                env['QC_ADVERTISER_IDS']='777'
                result = answer('预算计划',root,env,kb)
                evidence = json.loads(json.loads(request.call_args.args[0].data)['messages'][1]['content'])['检索资料']
                self.assertEqual(len(evidence),1)
                self.assertEqual(evidence[0]['文档'],'budget.md')
                self.assertNotIn('本地模型日报',result)

    def test_report_only_question_and_plan_beyond_first_twenty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            report={'audit':{'start':'2026-10-27','end':'2026-10-27','days':1},
                    'source_api':{'advertiser_id':'999'},'totals':{},'notes':[],
                    'plans':[{'plan_id':str(1000+i),'plan_name':'普通计划'+str(i)} for i in range(50)]}
            report['plans'][49]['plan_name']='特别马甲'
            context=json.loads(daily_knowledge_context(report,'1049计划如何调整'))
            self.assertEqual(len(context['逐计划']),20)
            self.assertEqual(context['逐计划'][0]['plan_id'],'1049')
            self.assertEqual(context['计划覆盖']['完整日报计划数'],50)
            self.assertEqual(json.loads(daily_knowledge_context(report,'特别马甲的预算'))['逐计划'][0]['plan_id'],'1049')
            # A saved report can ground Q&A even without any imported SOP hits.
            kb=Knowledge(root/'empty.db')
            env={'DEEPSEEK_API_KEY':'secret','DEEPSEEK_MODEL':'test'}
            class Response:
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def read(self): return json.dumps({'choices':[{'message':{'content':'数据不足，须核对预算。'}}]}).encode()
            with patch('urllib.request.urlopen',return_value=Response()) as request:
                self.assertIn('本地模型日报',kb.ask('1049计划',env,report_context=json.dumps(context)))
                request.assert_called_once()

if __name__ == '__main__': unittest.main()
