import json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape
from qc_shadow.profit_import import run_profit

class ImportTests(unittest.TestCase):
    def test_xlsx_to_agent_repeat_reopen_no_network_no_pii(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);source=home/'source.xlsx';profile=home/'profile.json'
            p={'version':'v1','account_id':'acct','start':'2026-10-21','end':'2026-10-21','shipping':'2.2','minimum_profit':None,'fees':{'x':'0.131'},'products':{'9000000000000009':{'style':'S','cost':'10'}}}
            profile.write_text(json.dumps(p))
            sheets=[('成本汇总',[['商品ID','实际成本'],['9000000000000009','10']]),
                ('S订单',[['主订单编号','子订单编号','商品ID','商品数量','订单应付金额','平台实际承担优惠金额','达人实际承担优惠金额','售后状态','支付完成时间','发货时间','收件人'],
                  ['9000000000000010','9000000000000011','9000000000000009','2','90','10','0','-','46316.5','46316.6','PRIVATE_NAME_DO_NOT_PERSIST']]),
                ('S计划',[['ad_id','日期','整体消耗'],['9000000000000000','全部','5'],['9000000000000000','46316','5']])]
            ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{n}" sheetId="{i}" r:id="r{i}"/>' for i,(n,_) in enumerate(sheets,1))+'</sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships>'+''.join(f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1,4))+'</Relationships>')
                for i,(_,data) in enumerate(sheets,1):
                    body=''.join(f'<row r="{rn}">'+''.join(f'<c r="{chr(65+cn)}{rn}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for cn,value in enumerate(row))+'</row>' for rn,row in enumerate(data,1))
                    z.writestr(f'xl/worksheets/sheet{i}.xml',f'<worksheet xmlns="{ns}"><sheetData>{body}</sheetData></worksheet>')
            args=[source,profile,home/'profit.db',home/'reports','2026-11-04T16:27:00+08:00']
            with patch('socket.socket',side_effect=AssertionError('network forbidden')):
                a=run_profit(*args);b=run_profit(*args)
            self.assertEqual(a['products'][0]['post_ad_profit'],'59.70')
            self.assertEqual(a['run_id'],b['run_id']);self.assertEqual(b['import']['changed'],0)
            text=(Path(a['output'])/'result.json').read_text();self.assertNotIn('PRIVATE_NAME',text)
            self.assertNotIn(b'PRIVATE_NAME', (home/'profit.db').read_bytes())
            self.assertIn('9000000000000011',text);self.assertFalse(a['execution_allowed'])
            v3=dict(p,version='v3',ad_basis='gross_gmv_rate',ad_rate='0.15',profit_target_rate='0.15')
            v3path=home/'v3.json';v3path.write_text(json.dumps(v3))
            with patch('socket.socket',side_effect=AssertionError('network forbidden')):
                c=run_profit(source,v3path,home/'v3.db',home/'v3reports','2026-11-04T16:27:00+08:00')
            self.assertEqual(c['products'][0]['post_ad_profit_actual'],'59.70')
            self.assertEqual(c['products'][0]['post_ad_profit_estimated'],'49.70')
            self.assertEqual(c['products'][0]['recommended_additional_spend'],'10.00')
            self.assertIn('已花5.00元',(Path(c['output'])/'群内提醒草稿.md').read_text())
            self.assertTrue((Path(c['output'])/'广告与利润对比.csv').is_file())
            p['account_id']='different';profile.write_text(json.dumps(p))
            with self.assertRaisesRegex(ValueError,'database_account_mismatch'):run_profit(*args)
