import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from agent.conversation import Conversation, is_followup
from agent import listener

class Tests(unittest.TestCase):
    def test_scope_expiry_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Conversation(Path(tmp)/'memory.db')
            m.save('a','u',list(range(5)))
            self.assertEqual(m.load('a','u'),[2,3,4])
            self.assertEqual(m.load('b','u'),[])
            self.assertEqual(m.load('a','v'),[])
            with patch('agent.conversation.time.time',return_value=99999999999):
                self.assertEqual(m.load('a','u'),[])
    def test_followup_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Conversation(Path(tmp)/'memory.db')
            m.save('c','u',[{'question':'日报总结','answer':'上轮回答','source':'2026-10-20数据'}])
            kb = Mock(last_context=None)
            def ask(q,e,report_context=None):
                self.assertIn('2026-10-20数据',report_context)
                self.assertIn('上轮回答',report_context)
                kb.last_context=report_context
                return '更简短的总结'
            kb.ask.side_effect=ask
            nf=Mock()
            with patch.object(listener,'Knowledge',return_value=kb), patch.object(listener,'_latest_report',side_effect=AssertionError('不能重读日报')):
                listener.reply_command(nf,'msg','能给我更简单的总结吗？',env={'KNOWLEDGE_ENABLED':'true'},context={'chat_id':'c','sender_id':'u'},memory=m)
            nf.reply_markdown.assert_called_once()
            self.assertEqual(m.load('c','u')[-1]['source'],'2026-10-20数据')
            self.assertFalse(is_followup('为什么达人视频审核不通过'))
            nf.reset_mock()
            listener.reply_command(nf,'m2','再简单点',context={'chat_id':'c','sender_id':'other'},memory=m)
            self.assertIn('引用',nf.reply_markdown.call_args.args[1])

if __name__ == '__main__': unittest.main()
