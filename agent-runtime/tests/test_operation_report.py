"""Grok operation intake: no external APIs or advertising writes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agent import operation_report as op,daily_brief as d,settings
from test_daily_brief import NOW,ENV,fixtures


def report():
    actions=[]
    for index,status in enumerate(['success','failed','unconfirmed']):
        actions.append({'action_id':'grok-'+str(index),'executed_at':'2026-10-28T%02d:00:00+08:00'%(12+index),
            'action_type':'budget_change','target_type':'plan','target_id':str(100+index),'target_name':'计划'+str(index),
            'before':'500元','after':'800元','status':status,'reason':'按既有投放流程调整',
            'evidence':'执行日志返回成功并回读预算800元（模拟）' if status=='success' else '执行结果尚未生效'})
    return {'schema_version':1,'advertiser_id':'999','report_date':'2026-10-28',
        'generated_at':'2026-10-29T09:55:00+08:00','complete':True,'source':'Grok Bot（模拟日志）',
        'summary':'模拟：1项成功、1项失败、1项待确认。','actions':actions}


class Tests(unittest.TestCase):
    def test_write_load_atomic_and_status_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=op.write(tmp,report(),'999','2026-10-28',NOW)
            self.assertEqual(target,Path(tmp)/'data/grok_operations/999/2026-10-28.json')
            loaded=op.load(tmp,'999','2026-10-28',NOW)
            self.assertEqual(loaded['state'],'ready');self.assertTrue(loaded['sha256'])
            text=str(op.elements(loaded))
            self.assertIn('成功1项、失败1项、待确认1项',text)
            self.assertIn('尝试/目标值，不代表已生效',text)
            self.assertEqual(len(list(target.parent.iterdir())),1)

    def test_missing_report_is_not_no_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            value=op.load(tmp,'999','2026-10-28',NOW)
            self.assertEqual(value['state'],'missing')
            self.assertIn('不代表昨日无操作',str(op.elements(value)))

    def test_explicit_empty_and_partial_reports_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            value=report();value['actions']=[];value['summary']='经日志核对没有操作。'
            op.write(tmp,value,'999','2026-10-28',NOW)
            self.assertIn('空操作记录',str(op.elements(op.load(tmp,'999','2026-10-28',NOW))))
            value['complete']=False;op.write(tmp,value,'999','2026-10-28',NOW)
            self.assertIn('部分记录',str(op.elements(op.load(tmp,'999','2026-10-28',NOW))))

    def test_wrong_account_day_duplicate_future_and_missing_evidence_rejected(self):
        changes=[lambda r:r.update(advertiser_id='888'),lambda r:r.update(report_date='2026-10-27'),
                 lambda r:r['actions'].append(copy.deepcopy(r['actions'][0])),
                 lambda r:r.update(generated_at='2026-10-29T23:59:00+08:00'),
                 lambda r:r['actions'][0].update(executed_at='2026-10-29T00:01:00+08:00'),
                 lambda r:r['actions'][0].update(evidence=''),lambda r:r['actions'][0].update(status='planned'),
                 lambda r:r.update(generated_at='2026-10-28T23:59:00+08:00')]
        for change in changes:
            value=report();change(value)
            with self.assertRaises((ValueError,TypeError)):op.validate(value,'999','2026-10-28',NOW)

    def test_secret_fields_and_cross_timezone_dates(self):
        value=report();value['summary']='access_token=private'
        with self.assertRaises(ValueError):op.validate(value,'999','2026-10-28',NOW)
        value=report();value['actions'][0]['executed_at']='2026-10-28T20:00:00-05:00'
        with self.assertRaises(ValueError):op.validate(value,'999','2026-10-28',NOW)
        with self.assertRaises(ValueError):op.report_path('.', '../wrong','2026-10-28')

    def test_invalid_report_does_not_expose_raw_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=op.report_path(tmp,'999','2026-10-28');path.parent.mkdir(parents=True)
            path.write_text('{broken SECRET_CONTENT',encoding='utf-8')
            value=op.load(tmp,'999','2026-10-28',NOW)
            self.assertEqual(value['state'],'invalid')
            self.assertNotIn('SECRET_CONTENT',str(op.elements(value)))

    def test_top_actions_are_bounded_but_full_records_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            op.write(tmp,report(),'999','2026-10-28',NOW)
            value=op.load(tmp,'999','2026-10-28',NOW)
            text=str(op.elements(value,1))
            self.assertIn('1/3项',text);self.assertIn('失败 调整预算',text)
            self.assertEqual(len(value['report']['actions']),3)

    def test_daily_prepare_merges_and_archives_without_external_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir();cfg=settings.load_config()
            op.write(root,report(),'999','2026-10-28',NOW)
            with patch('urllib.request.urlopen',side_effect=AssertionError('no network')),\
                 patch.object(d.qianchuan,'fetch_daily',return_value=fixtures()):
                card=d.prepare(root,ENV,NOW,cfg,d.options(root))
            self.assertIn('Grok昨日操作',str(card));self.assertIn('成功1项',str(card))
            saved=[p for p in (root/'out').glob('model_*.json') if not p.name.endswith('_cards.json')]
            data=json.loads(saved[0].read_text(encoding='utf-8'))
            self.assertEqual(len(data['operation_report']['report']['actions']),3)


if __name__=='__main__':unittest.main()
