from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from agent.feedback import publish, reply


class FeedbackPauseTests(TestCase):
    def test_pause_blocks_publish_and_reply_before_other_work(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir();(root/'config/feedback.paused').touch()
            service=Mock();service.ledger=SimpleNamespace(path=root/'data/feedback.sqlite')
            notifier=Mock()
            self.assertEqual(publish(service,'synthetic',notifier),{'status':'paused','id':'synthetic'})
            self.assertIsNone(reply(notifier,'synthetic','反馈',root,{},{}))
            service.scoped.assert_not_called()
            self.assertEqual(notifier.mock_calls,[])
