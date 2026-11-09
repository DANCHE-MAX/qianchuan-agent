import unittest
from agent.analysis_gate import finding_text

class RoiDisplay(unittest.TestCase):
    def test_zero_is_readable_and_distinct_from_unknown(self):
        base=dict(name='虚构计划',id='synthetic',reasons=['观察'],spend='10',gmv='0',orders='0')
        for value in ('0E+2', '0', 0):
            self.assertIn('ROI 0.00。', finding_text(dict(base,payment_roi=value)))
        self.assertIn('ROI 暂无。', finding_text(dict(base,payment_roi=None)))
        self.assertIn('ROI 1.23。', finding_text(dict(base,payment_roi='1.234')))
