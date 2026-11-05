"""模型核算、缺数、截断和真实导出回归；零网络。"""
import copy
import json
import os
import sys
import tempfile
import io
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import model, settings
import main as app


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.cfg = settings.load_config()['model']
        self.cfg['plans'] = {'9000000000000008': {'refund_band': 'gt300', 'basis': 'synthetic test fixture'}}
        self.rows = [{'计划ID':'9000000000000008','计划名称':'马甲','日期':'2026-10-%02d'%i,
                      '小时区间':'全部','整体成交金额':'10,000','净成交金额':'5,000',
                      '整体消耗':'1538','综合成本':'1600'} for i in range(15,21)]

    def evaluate(self, rows=None, cfg=None):
        return model.evaluate(self.rows if rows is None else rows, cfg or self.cfg,
                              'autumn_winter','2026-10-20',today=date(2026,10,22))

    def test_user_cost_example_and_separate_denominator(self):
        cfg=copy.deepcopy(self.cfg)
        cfg['refund_rates']['autumn_winter']['gt300']=.5
        r=self.evaluate(cfg=cfg);p=r['plans'][0]
        self.assertEqual(p['predicted_net'],D(30000))
        self.assertEqual(p['purchase_cost'],D('9375'))
        self.assertEqual(p['insurance_cost'],D('801'))
        self.assertEqual(p['platform_cost'],D('3126'))
        self.assertEqual(p['commission_cost'],D('0'))
        self.assertEqual(r['advertising_margin'],D('0.5566'))
        self.assertEqual(p['predicted_profit'],D(1245)*6)
        self.assertEqual(p['comprehensive_roi'],D('3.125'))
        self.assertEqual(p['payment_roi'],D(10000)/D(1538))
        self.assertEqual(r['totals']['payment_roi'],D(60000)/D(9228))
        self.assertEqual(p['daily_cap_reference'],D(1500))
        self.assertIsNone(p['configured_budget'])

    def test_filter_and_fixed_six_day_divisor(self):
        extra=[dict(self.rows[0],日期='全部'),dict(self.rows[0],小时区间='00:00 ~ 01:00'),dict(self.rows[0],日期='2026-10-21')]
        r=self.evaluate(self.rows+extra)
        self.assertEqual(r['totals']['gmv'],D(60000))
        self.assertEqual(r['audit']['selected_rows'],6)
        self.assertEqual(r['audit']['outside_window_rows'],1)

    def test_missing_date_and_duplicate_fail(self):
        with self.assertRaisesRegex(ValueError,'缺少每日'):
            self.evaluate(self.rows[:-1])
        with self.assertRaisesRegex(ValueError,'重复计划'):
            self.evaluate(self.rows+[self.rows[0]])

    def test_current_day_rejected(self):
        with self.assertRaisesRegex(ValueError,'当日'):
            model.evaluate(self.rows,self.cfg,'autumn_winter','2026-10-20',today=date(2026,10,20))

    def test_unknown_price_blocks_total_prediction(self):
        rows=[dict(r,计划ID='new') for r in self.rows]
        r=self.evaluate(rows)
        self.assertIsNone(r['totals']['predicted_profit'])
        self.assertEqual(r['totals']['gmv'],D(60000))
        self.assertEqual(r['plans'][0]['status'],'需补价格档')

    def test_zero_gmv_loss_included_and_zero_denominator(self):
        rows=[dict(r,计划ID='new',整体成交金额='0',净成交金额='0') for r in self.rows]
        self.assertEqual(self.evaluate(rows)['totals']['predicted_profit'],-D(1538)*6)
        rows=[dict(r,整体消耗='0',综合成本='0') for r in rows]
        self.assertIsNone(self.evaluate(rows)['totals']['comprehensive_roi'])

    def test_invalid_numbers_fail_not_zero(self):
        for bad in ('-', 'NaN','Infinity','-1','oops'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.evaluate([dict(r,整体消耗=bad) for r in self.rows])

    def test_missing_rows_for_one_plan_does_not_change_denominator(self):
        extra=dict(self.rows[0],计划ID='new',整体成交金额='0',净成交金额='0',整体消耗='60',综合成本='60')
        p=next(p for p in self.evaluate(self.rows+[extra])['plans'] if p['plan_id']=='new')
        self.assertEqual(p['daily_spend'],D(10))

    def test_weighted_overall_roi(self):
        extra=dict(self.rows[0],计划ID='new',整体成交金额='1000',净成交金额='1000',整体消耗='10',综合成本='10')
        r=self.evaluate(self.rows+[extra])
        self.assertEqual(r['totals']['comprehensive_roi'],D(31000)/D(9610))

    def test_report_roundtrip_and_pagination(self):
        r=self.evaluate();text=model.render(r)
        chunks=model.message_chunks(text,1000)
        self.assertEqual(''.join(chunks),text)
        self.assertTrue(all(len(c.encode('utf-8'))<=1000 for c in chunks))
        self.assertNotIn('净ROI',text)
        with tempfile.TemporaryDirectory() as tmp:
            p=model.save(r,tmp)
            saved=json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(D(saved['totals']['gmv']),D(60000))

    def test_real_export_reconciliation(self):
        path=Path(os.environ.get('QC_TEST_EXPORT',''))
        if not path.is_file():
            self.skipTest('QC_TEST_EXPORT未设置')
        r=self.evaluate(model.read_daily(path))
        self.assertEqual(r['totals']['ad_spend'],D('8367.37'))
        self.assertEqual(r['totals']['gmv'],D('61489.32'))
        self.assertEqual(r['totals']['backend_net'],D('48055.23'))
        self.assertEqual(len(r['plans']),8)
        self.assertEqual(model.fmt(r['totals']['predicted_profit']),'3,391.38')
        self.assertEqual(model.fmt(r['totals']['daily_cap_reference']),'1,537.23')
        self.assertEqual(model.fmt(r['totals']['comprehensive_roi']),'5.74')

    def test_dry_failure_never_sends(self):
        with patch.object(sys,'argv',['main.py','model','--input','missing.xlsx','--date','2026-10-20']), \
             patch.object(app,'_report_failure') as send, patch.object(app.settings,'load_env',return_value={}), \
             patch('sys.stderr',new_callable=io.StringIO):
            self.assertEqual(app.main(),1)
            send.assert_not_called()

    def test_missing_input_with_send_only_reports_locally(self):
        with patch.object(sys,'argv',['main.py','daily','--input','最新完整计划报表.xlsx',
                                      '--date','2026-10-20','--send']), \
             patch.object(app,'_report_failure') as alert, \
             patch('sys.stderr',new_callable=io.StringIO) as err:
            self.assertEqual(app.main(),1)
            alert.assert_not_called()
            self.assertIn('找不到计划报表',err.getvalue())
            self.assertNotIn('Traceback',err.getvalue())

    def test_known_partial_export_rejected_even_on_later_day(self):
        path=self.cfg['input_file']
        with patch.object(sys,'argv',['main.py','model','--input',path,'--date','2026-10-21']), \
             patch.object(app,'_report_failure') as send, patch.object(app.settings,'load_env',return_value={}), \
             patch('sys.stderr',new_callable=io.StringIO):
            self.assertEqual(app.main(),1)
            send.assert_not_called()

    def test_price_boundaries_are_explicit_configured_profiles(self):
        r=self.evaluate()
        self.assertEqual(r['plans'][0]['refund_band'],'gt300')
        summer=model.evaluate(self.rows,self.cfg,'summer','2026-10-20',today=date(2026,10,22))
        self.assertLess(summer['plans'][0]['predicted_net'],r['plans'][0]['predicted_net'])

    def test_existing_commands_route_to_model(self):
        with patch.object(app,'cmd_model',return_value=0) as target:
            self.assertEqual(app.cmd_daily(object()),0)
            self.assertEqual(app.cmd_suggest(object()),0)
            self.assertEqual(target.call_count,2)

    def test_cli_preview_and_explicit_feishu_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp,'test.xlsx')
            source.write_bytes(b'')  # 本测试只模拟解析；路径本身必须真实存在
            argv=['main.py','model','--input',str(source),'--date','2026-10-20','--output-dir',tmp]
            with patch.object(sys,'argv',argv+['--dry']), \
                 patch.object(model,'read_daily',return_value=self.rows), \
                 patch.object(app.settings,'load_env',return_value={}), \
                 patch.object(app.notify,'build_notifier') as build, \
                 patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(app.main(),0)
                build.assert_not_called()
            with patch.object(sys,'argv',argv+['--send']), \
                 patch.object(model,'read_daily',return_value=self.rows), \
                 patch.object(app.settings,'load_env',return_value={}), \
                 patch.object(app.notify,'build_notifier') as build, \
                 patch('sys.stdout',new_callable=io.StringIO):
                build.return_value.text_limit=20000
                build.return_value.md_limit=20000
                self.assertEqual(app.main(),0)
                self.assertTrue(build.return_value.send_card.called)
                build.return_value.send_markdown.assert_not_called()
                sent=build.return_value.send_card.call_args_list
                self.assertEqual(len(sent),2)
                text=json.dumps([c.args[0] for c in sent],ensure_ascii=False)
                self.assertIn('整体支付 ROI',text)
                self.assertIn('后台实测',text)
                self.assertIn('历史退款情景预测',text)
                self.assertTrue(Path(tmp,'model_2026-10-15_2026-10-20_autumn_winter_cards.json').exists())
            with patch.object(sys,'argv',argv+['--send']), \
                 patch.object(model,'read_daily',return_value=self.rows), \
                 patch.object(app.settings,'load_env',return_value={}), \
                 patch.object(app.notify,'build_notifier') as build, \
                 patch.object(app,'_report_failure') as alert, \
                 patch('sys.stdout',new_callable=io.StringIO), \
                 patch('sys.stderr',new_callable=io.StringIO) as err:
                build.return_value.send_card.side_effect=RuntimeError('HTTP 400 code=230001')
                self.assertEqual(app.main(),1)
                alert.assert_not_called()
                self.assertIn('第 1/2 张卡片发送失败',err.getvalue())


if __name__=='__main__':
    unittest.main()
