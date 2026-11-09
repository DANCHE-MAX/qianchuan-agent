"""Generate the business overview SVG from documentation text only."""
from pathlib import Path
from html import escape
out=Path(__file__).resolve().parents[1]/'docs/assets'
sections=[
('01','看经营结果','昨日赚不赚钱','查看预估利润与亏损商品','今天优先看谁','盈利、亏损、花费各列最多三项'),
('02','看钱花在哪','按商品看','哪些商品的花费增加或减少','按时段看','对比两天各时段的花费变化','花费是否值得','对比盈利贡献和广告花费占比'),
('03','看资金风险','余额还能用多久','结合当前消耗速度估算','花钱是否变快','对比前两小时和昨日同时段'),
('04','在飞书接收结果','每天看日报','重点导读、利润图、两日对比','需要时查明细','Excel 查看每个计划','及时收提醒','同批规则异常合并，资金提醒单列','直接问昨日结果','谁赚得多、谁亏得多、钱花在哪'),
('05','跟进数据缺口','本地登记待核验','区分真实数据、业务补值与缺失','后续复查并记录差额','复查最近7个完整自然日，旧报表不改'),
('06','明确使用边界','目前由投手决策','不自动改预算、ROI 或暂停计划','利润仍是预估','最终结果需核对实际退款与费用','后续继续完善','跟踪建议效果与节省的工时')]
parts=['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="2120" viewBox="0 0 900 2120"><rect width="900" height="2120" fill="#f3f6fa"/><style>text{font-family:"PingFang SC","Microsoft YaHei",sans-serif}</style>']
def text(x,y,s,size=28,color='#34445a',weight=400):
 parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{escape(s)}</text>')
parts.append('<rect x="40" y="36" width="820" height="172" rx="24" fill="#183a59"/>')
text(76,101,'千川投放助手',44,'#ffffff',700)
text(76,153,'业务功能一览 · 看清投放，再做判断',28,'#d8e7f4')
parts.append('<path d="M76 208 V__LAST__" stroke="#b5c9d9" stroke-width="4" fill="none"/>')
y=246
for sec in sections:
 n,title,*items=sec
 h=110+88*(len(items)//2)
 last=y+43
 parts.append(f'<path d="M76 {y+43} H112" stroke="#b5c9d9" stroke-width="4"/><circle cx="76" cy="{y+43}" r="9" fill="#277a9b"/>')
 parts.append(f'<rect x="112" y="{y}" width="748" height="{h}" rx="20" fill="white" stroke="#dce5ed"/>')
 text(140,y+54,n,26,'#277a9b',700);text(194,y+54,title,34,'#183a59',700)
 parts.append(f'<path d="M151 {y+87} V{y+h-57}" stroke="#d4e2eb" stroke-width="2"/>')
 for i in range(0,len(items),2):
  yy=y+106+(i//2)*88
  parts.append(f'<path d="M151 {yy-9} H169" stroke="#d4e2eb" stroke-width="2"/>')
  text(182,yy,items[i],28,'#183a59',600)
  text(182,yy+39,items[i+1],27,'#53657a')
 y+=h+24
text(112,y+26,'助手负责分析与提醒，投手负责判断与调整。',27,'#53657a')
parts.append('</svg>')
svg=''.join(parts).replace('__LAST__',str(last))
# Fit canvas to content, with a clear bottom margin.
height=y+72
svg=svg.replace('height="2120"','height="%s"'%height).replace('0 0 900 2120','0 0 900 %s'%height)
(out/'business-tree.svg').write_text(svg)
(Path('/tmp/qc-business-tree-preview.html')).write_text('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;width:900px;background:#f3f6fa}svg{display:block}</style>'+svg)
print(height)
