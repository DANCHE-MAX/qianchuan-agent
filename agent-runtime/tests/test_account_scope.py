import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import listener, model, delivery
from agent.conversation import Conversation


def report(account, end):
    r={'audit':{'start':end,'end':end,'days':1},'season':'autumn_winter', 'totals':{},'plans':[]}
    if account:r['source_api']={'advertiser_id':account}
    return r


class Tests(unittest.TestCase):
    def test_latest_report_filters_account_not_only_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i,r in enumerate([report('111','2026-10-28'),report('222','2026-10-27'),report(None,'2026-10-29')]):
                Path(tmp,'model_%d.json'%i).write_text(json.dumps(r),encoding='utf-8')
            r=listener._latest_report(tmp,account='222')
            self.assertEqual(r['source_api']['advertiser_id'],'222')
            self.assertIsNone(listener._latest_report(tmp,account='333'))
            self.assertIn('当前账户',listener.answer('报告',tmp,env={'QC_ADVERTISER_IDS':'333'}))

    def test_conversation_does_not_carry_other_account_or_untagged_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'conversation.db'
            Conversation(path).save('chat','u',[{'question':'legacy','source':'old'}])
            first=Conversation(path,account='111')
            second=Conversation(path,account='222')
            self.assertEqual(first.load('chat','u'),[])
            first.save('chat','u',[{'question':'balance','source':'111data'}])
            self.assertTrue(first.load('chat','u'))
            self.assertEqual(second.load('chat','u'),[])
            second.save('chat','u',[{'question':'balance','source':'222data'}])
            self.assertEqual(second.load('chat','u')[-1]['advertiser_id'],'222')

    def test_report_filenames_include_account_and_preserve_legacy(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(model,'render',return_value='test'):
            a=model.save(report('111','2026-10-27'),tmp)
            b=model.save(report('222','2026-10-27'),tmp)
            legacy=model.save(report(None,'2026-10-27'),tmp)
            self.assertEqual(len(list(Path(tmp).glob('*.json'))),3)
            self.assertNotEqual(a,b)
            self.assertIn('model_111_',a.name)
            self.assertEqual(legacy.name,'model_2026-10-27_2026-10-27_autumn_winter.md')

    def test_old_pending_replies_not_replayed_in_new_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox=listener.Inbox(Path(tmp)/'inbox.db')
            box=delivery.Outbox(Path(tmp)/'replies.db')
            for mid,ctx in [('old',{'advertiser_id':'111'}),('untagged',{})]:
                inbox.add(mid,'报告',context=ctx)
            nf=Mock()
            with patch.object(delivery,'dispatch',side_effect=AssertionError('old account')):
                listener.process_one(inbox,nf,box,tmp,{'QC_ADVERTISER_IDS':'222'})
                listener.process_one(inbox,nf,box,tmp,{'QC_ADVERTISER_IDS':'222'})
            self.assertFalse(nf.method_calls)
            self.assertIsNone(inbox.next())


if __name__=='__main__':unittest.main()
