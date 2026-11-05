import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const input=process.argv[2],dir=path.dirname(input),d=JSON.parse(await fs.readFile(input,'utf8'));
const wb=Workbook.create(),s=wb.worksheets.add('昨日计划汇总'),b=wb.worksheets.add('全部计划测算'),a=wb.worksheets.add('模型口径与覆盖'),q=wb.worksheets.add('缺失与对账');
const set=(sh,c,v)=>sh.getRange(c).values=[[v]],header=(sh,r,values)=>{sh.getRange(`A${r}`).write([values]);sh.getRangeByIndexes(r-1,0,1,values.length).format={fill:'#172B44',font:{color:'#FFFFFF',bold:true},rowHeight:45,wrapText:true};};
const last=7+d.rows.length;
for(const sh of [s,b,a,q]){sh.showGridLines=false;sh.getRange(`A1:P${last+10}`).format.font={name:'Arial',size:11,color:'#172B44'};sh.getRange(`A1:P${last+10}`).format.rowHeight=30;sh.getRange(`A1:A${last+10}`).format.columnWidth=56;sh.getRange(`B1:P${last+10}`).format.columnWidth=19;sh.freezePanes.freezeRows(6);}
set(s,'A2',`${d.date} 昨日计划预估净利润`);s.getRange('A2').format.font={size:17,bold:true};
set(s,'A3',`共${d.rows.length}个计划，${d.calculable}个可测算；其余利润待核验，合计仅覆盖可测算部分。`);
set(s,'A4','历史情景估算；今天可执行追加额度未确认。');
header(s,6,['商品 / 计划名称','实际广告费','预估净利润','退款前GMV','15%广告参考','15%情景利润','历史保本空间','投放判断','历史候选追加','今日可执行追加']);
header(b,6,['商品 / 计划名称','计划ID','数据核查','退款前GMV','实际广告费','预计退款后收入','估算商品成本','估算其他费用','投前利润','预估净利润','15%广告参考','15%情景利润','历史保本空间','投放判断','历史候选追加','今日可执行追加']);
set(b,'A2',`${d.date} 全部计划测算（人民币元）`);set(b,'A3','无明细不等于0；官方确认无消耗仅补广告费0，不推算缺失GMV。');
set(a,'A2','现行模型参数和覆盖范围');header(a,5,['参数','数值','来源']);
a.getRange('A6:C9').values=[['秋冬整体退款率',Number(d.policy.refund_rate),d.policy_version],['采购倍率',Number(d.policy.purchase_multiplier),'本地已确认策略'],['其他费用比例',Number(d.policy.fee_rate),'退款后预计收入为基数'],['广告参考比例',Number(d.policy.ad_rate),'退款前GMV为基数']];
a.getRange('B6').setNumberFormat('0.0000%');a.getRange('B8:B9').setNumberFormat('0.00%');a.getRange('B6:B9').format.font.color='#215DB0';a.getRange('C1:C30').format.columnWidth=60;
const notes=[`数据日期：${d.date}；采集时间：${d.as_of}`,`账户：${d.account}；来源：千川官方只读API，原始响应保存在同目录source文件夹。`,'经营净收入按千川含券GMV和已确认季节整体退款率估算，不依赖抖店或发货数量。','商品成本=预计退款后收入÷采购倍率；本次不计运费及人员、租金等固定费用。','展示的预估净利润为情景投后贡献利润，不是公司财务净利润。','实际广告费与15%情景广告费分别扣除，不能叠加。','历史候选追加=min(投前利润,15%广告参考)−实际消耗，负值取0。','不同计划不能互相挪用保本空间；今天可执行额度全部待核验。','反馈及机器人交互暂停；日报没有预算执行操作。',...d.warnings];
notes.forEach((x,i)=>set(a,`A${12+i}`,x));
let sr=7,qr=7;
set(q,'A2',`${d.date} 缺失与对账`);header(q,6,['商品 / 计划名称','计划ID','核查结论','已核实广告费','说明 / 来源']);
d.rows.forEach((p,i)=>{const r=i+7;set(b,`A${r}`,p.name);set(b,`B${r}`,p.plan_id);set(b,`C${r}`,p.status);set(b,`D${r}`,p.gmv===null?'待核验':Number(p.gmv));set(b,`E${r}`,p.spend===null?'待核验':Number(p.spend));set(b,`P${r}`,'待核验');
 if(p.profit===null){b.getRange(`F${r}:O${r}`).values=[Array(10).fill('待核验')];q.getRange(`A${qr}:E${qr}`).values=[[p.name,p.plan_id,p.status,p.spend===null?'待核验':Number(p.spend),p.spend==='0.00'?'官方having_cost=NO确认无消耗；缺失GMV不补0。':'昨日接口未返回完整数据；不以当前状态推断昨日。']];qr++;}
 else{const fm={F:`ROUND(D${r}*(1-'模型口径与覆盖'!$B$6),2)`,G:`ROUND(F${r}/'模型口径与覆盖'!$B$7,2)`,H:`ROUND(F${r}*'模型口径与覆盖'!$B$8,2)`,I:`F${r}-G${r}-H${r}`,J:`I${r}-E${r}`,K:`ROUND(D${r}*100*'模型口径与覆盖'!$B$9,0)/100`,L:`I${r}-K${r}`,M:`MAX(0,J${r})`,N:`IF(J${r}<0,"预估亏损，不追加",IF(E${r}>=K${r},"达到15%参考，不追加","存在历史空间，人工评估"))`,O:`MAX(0,MIN(I${r},K${r})-E${r})`};for(const [col,f]of Object.entries(fm))b.getRange(`${col}${r}`).formulas=[['='+f]];
 s.getRange(`A${sr}:J${sr}`).formulas=[['A','E','J','D','K','L','M','N','O','P'].map(col=>`='全部计划测算'!${col}${r}`)];sr++;}
});
set(s,`A${sr}`,'可测算计划合计');for(const col of ['B','C','D','E','F','G','I']){if(sr===7)set(s,`${col}${sr}`,'待核验');else s.getRange(`${col}${sr}`).formulas=[[`=SUM(${col}7:${col}${sr-1})`]];}
s.getRange(`A${sr}:J${sr}`).format.fill='#EEF3F9';
const cr=qr+3;header(q,cr,['报表主题','指标','独立汇总','差额','核查结果']);d.checks.forEach((c,i)=>q.getRange(`A${cr+i+1}:E${cr+i+1}`).values=[[c.topic,c.metric,c.aggregate===null?'待核验':Number(c.aggregate),c.difference===null?'待核验':Number(c.difference),c.difference===null?'无有效汇总，不能确认':Number(c.difference)===0?'一致':'差异待核验']]);
for(const [sh,end,profitcol]of [[s,sr-1,'C'],[b,last-1,'J']]){if(end>=7){const names=sh.getRange(`A7:A${end}`);names.format.fill='#E5E7EB';names.conditionalFormats.addCustom(`AND(ISNUMBER($${profitcol}7),$${profitcol}7>0)`,{fill:'#14975D',font:{color:'#FFFFFF',bold:true}});names.conditionalFormats.addCustom(`AND(ISNUMBER($${profitcol}7),$${profitcol}7<0)`,{fill:'#D83F45',font:{color:'#FFFFFF',bold:true}});sh.getRange(`A7:P${end}`).format.rowHeight=56;sh.getRange(`A7:P${end}`).format.wrapText=true;}}
b.getRange(`B7:B${last}`).setNumberFormat('@');q.getRange(`B7:B${Math.max(qr,7)}`).setNumberFormat('@');b.getRange(`D7:M${last}`).setNumberFormat('#,##0.00;[Red](#,##0.00);0.00');b.getRange(`O7:O${last}`).setNumberFormat('#,##0.00');s.getRange(`B7:I${sr}`).setNumberFormat('#,##0.00;[Red](#,##0.00);0.00');
b.getRange(`C1:C${last}`).format.columnWidth=34;b.getRange(`N1:N${last}`).format.columnWidth=33;s.getRange(`H1:H${sr}`).format.columnWidth=33;q.getRange(`C1:C${cr+8}`).format.columnWidth=34;q.getRange(`E1:E${cr+8}`).format.columnWidth=65;q.getRange(`A7:E${cr+8}`).format.wrapText=true;q.getRange(`A7:E${cr+8}`).format.rowHeight=55;
wb.recalculate();
d.rows.forEach((p,i)=>{if(p.profit!==null)for(const [col,key]of [['J','profit'],['K','ad15'],['L','profit15'],['M','headroom'],['O','candidate']]){if(Math.abs(b.getRange(col+(i+7)).values[0][0]-Number(p[key]))>.001)throw Error('Formula reconciliation failed: '+key);}});
if(d.calculable>0&&Math.abs(s.getRange('C'+sr).values[0][0]-Number(d.totals_calculable_only.profit))>.001)throw Error('Summary mismatch');
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!',options:{useRegex:true,maxResults:20}})).ndjson);
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(dir,`profit-${d.date}.xlsx`));
if(process.env.PROFIT_RENDER_REVIEW==='1')for(const [sh,range] of [[s,`A1:J${Math.min(sr,20)}`],[b,'A1:P13'],[a,'A1:C24'],[q,'A1:E13']]){const im=await wb.render({sheetName:sh.name,range,scale:1,format:'png'});await fs.writeFile(path.join(dir,'review-'+sh.name+'.png'),new Uint8Array(await im.arrayBuffer()));}
