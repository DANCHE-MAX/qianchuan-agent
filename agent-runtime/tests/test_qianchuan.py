"""API pagination, amount semantics and single-day report/summary regression; no network."""
import copy
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import qianchuan as qc, settings, model, cards, listener
import main as app


def row(pid='9000000000000008', day='2026-10-24'):
    return {'dimensions': {'ad_id': {'Value': pid}, 'ad_name': {'Value': '马甲'},
                           'stat_time_day': {'ValueStr': day}},
            'metrics': {field: {'Value': value, 'ValueStr': 'ignored'} for field, value in zip(
                qc.METRICS.values(), [100, 1000, 800, 110])}}


def page(rows, number=1, total=1, pages=1):
    return {'code': 0, 'data': {'rows': rows, 'page_info': {
        'page': number, 'total_number': total, 'total_page': pages}}}


class QianchuanTests(unittest.TestCase):
    env = {'QC_ACCESS_TOKEN': 'test-secret', 'QC_ADVERTISER_IDS': '123'}

    def fetch(self, request, days=1):
        return qc.fetch_daily(self.env, '2026-10-24', days,
                              today=date(2026, 10, 26), request=request)

    def test_all_pages_amounts_and_single_day_math(self):
        request = Mock(side_effect=[page([row()], total=2, pages=2),
                                    page([row('other')], 2, 2, 2)])
        # Plan identifiers must remain numeric; large IDs are strings, never floats.
        request.side_effect = [page([row()], total=2, pages=2),
                               page([row('9000000000000007')], 2, 2, 2)]
        rows, meta = self.fetch(request)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['整体成交金额'], '1000')
        self.assertEqual(rows[0]['净成交金额'], '800')
        self.assertEqual(request.call_args_list[1].args[0]['page'], 2)
        self.assertEqual(request.call_args.args[0]['start_time'], '2026-10-24 00:00:00')
        cfg = dict(settings.load_config()['model'], window_days=1)
        result = model.evaluate(rows, cfg, 'autumn_winter', '2026-10-24')
        self.assertEqual(result['totals']['daily_spend'], Decimal(200))
        self.assertEqual(result['totals']['daily_cap_reference'], Decimal(300))
        self.assertEqual(result['totals']['comprehensive_roi'], Decimal(1600)/220)
        self.assertEqual(result['audit']['days'], 1)
        built = cards.build(result)
        self.assertIn('单日', built[0]['header']['title']['content'])
        self.assertNotIn('六日', json.dumps(built, ensure_ascii=False))
        self.assertNotIn('test-secret', json.dumps(meta))

    def test_empty_error_missing_field_and_bad_pagination_fail_closed(self):
        invalid = row()
        del invalid['metrics']['stat_cost_for_overall_roi2']
        for payload in [page([], total=0, pages=0), {'code': 50000}, page([invalid]),
                        page([row()], total=2), page([row(day='2026-10-25')]),
                        page([row(), row()], total=2)]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.fetch(Mock(return_value=payload))
        request = Mock(side_effect=[page([row()], total=2, pages=2),
                                    page([row('123')], 2, 3, 2)])
        with self.assertRaisesRegex(ValueError, '分页'):
            self.fetch(request)

    def test_current_day_and_multiday_gaps_rejected(self):
        request = Mock()
        with self.assertRaisesRegex(ValueError, '完整日'):
            qc.fetch_daily(self.env, '2026-10-26', today=date(2026, 10, 26), request=request)
        request.assert_not_called()
        with self.assertRaisesRegex(ValueError, '缺少每日'):
            self.fetch(Mock(return_value=page([row()])), days=6)

    def test_snapshot_hash_and_cached_cli_bot_summary(self):
        rows, meta = self.fetch(Mock(return_value=page([row()])))
        with tempfile.TemporaryDirectory() as tmp:
            path = qc.save_snapshot(rows, meta, tmp)
            self.assertEqual(qc.provenance(path)['advertiser_id'], '123')
            cfg = copy.deepcopy(settings.load_config())
            cfg['model']['input_file'] = str(path)
            cfg['model']['window_days_by_file'] = {path.name: 1}
            cfg['model']['complete_through_by_file'][path.name] = '2026-10-24'
            args = Namespace(date=None, input=None, source=None, output_dir=tmp)
            with patch.object(settings, 'load_config', return_value=cfg), \
                 patch.object(settings, 'load_env', return_value={}), redirect_stdout(io.StringIO()):
                self.assertEqual(app.cmd_model(args), 0)
            report = listener._latest_report(tmp)
            self.assertEqual(report['audit']['start'], '2026-10-24')
            self.assertEqual(report['audit']['end'], '2026-10-24')
            self.assertEqual(report['audit']['days'], 1)
            self.assertEqual(report['source_api']['advertiser_id'], '123')
            self.assertIn('1个完整日', listener.answer('报告', tmp))
            kb = Mock()
            kb.ask.return_value = '测试总结'
            listener.answer('总结日报', tmp, {'KNOWLEDGE_ENABLED': 'true'}, kb)
            context = json.loads(kb.ask.call_args.kwargs['report_context'])
            self.assertEqual(context['数据窗口']['days'], 1)
            self.assertEqual(context['整体']['gmv'], '1000')
            self.assertNotIn('六日聚合', context['限制'])
            path.write_text('changed', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '已改动'):
                qc.provenance(path)


if __name__ == '__main__':
    unittest.main()
