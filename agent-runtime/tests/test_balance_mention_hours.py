"""Routine mentions follow Beijing business hours; alerts remain actionable."""
import json
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone
from agent import monitor as m

CONFIG={'notify':{'roles':{'投手':['ou_synthetic']}},'monitoring':{'at_roles':{'balance_check':['投手'],'balance_alert':['投手'],'anomaly':['投手']}}}

class Tests(unittest.TestCase):
    def mentioned(self, category, at):
        rendered=m.add_mentions(m.card('balance',[]),CONFIG,category,now=at)
        return '<at id=' in json.dumps(rendered)

    def test_all_twelve_windows(self):
        for hour in range(0,24,2):
            with self.subTest(hour=hour):
                self.assertEqual(self.mentioned('balance_check',datetime(2026,11,7,hour,tzinfo=m.h.CHINA)),hour in (10,12,14,16,18))

    def test_utc_converted_to_beijing(self):
        self.assertTrue(self.mentioned('balance_check',datetime(2026,11,7,2,tzinfo=timezone.utc)))
        self.assertFalse(self.mentioned('balance_check',datetime(2026,11,7,12,tzinfo=timezone.utc)))

    def test_alerts_not_silenced_at_night(self):
        for category in ('balance_alert','anomaly'):
            self.assertTrue(self.mentioned(category,datetime(2026,11,7,0,tzinfo=m.h.CHINA)))

if __name__ == "__main__":
    unittest.main()
