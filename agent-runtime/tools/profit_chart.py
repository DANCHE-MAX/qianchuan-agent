"""Data chart, zero baseline and identical scales across pages; missing isn't zero."""
import json,sys,math
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
source=Path(sys.argv[1]);data=json.loads(source.read_text());font=sys.argv[2]
rows=[p for p in data['rows'] if p['profit'] is not None and p.get('gmv_source')!='business_rule_zero_spend']
limit=max([abs(float(p['profit'])) for p in rows]+[1]);step=max(1,10**math.floor(math.log10(limit))/2);upper=math.ceil(limit/step)*step
for page in range(max(1,math.ceil(len(rows)/12))):
 im=Image.new('RGB',(1500,2100),'white');d=ImageDraw.Draw(im)
 def text(x,y,t,size=28,color='#172B44',center=False):
  f=ImageFont.truetype(font,size);w=d.textlength(t,font=f);d.text((x-w/2 if center else x,y),t,font=f,fill=color)
 text(60,45,'昨日计划预估净利润',52);text(60,120,data['date']+' · 历史退款情景估算',32)
 t=data['totals_calculable_only'];text(60,185,'已花广告费 '+('¥'+t['spend'] if t['spend'] is not None else '待核验'),40);text(750,185,'预估利润 '+('¥'+t['profit'] if t['profit'] is not None else '待核验'),40)
 text(60,255,f'共{len(data["rows"])}个计划，{data["calculable"]}个可测算；{data["missing_profit"]}个利润待核验。',31)
 imputed=sum(p.get('gmv_source')=='business_rule_zero_spend' for p in data['rows'])
 if imputed:text(60,355,f'{imputed}个零值为业务规则补值，详见Excel；不作为接口实际GMV展示。',27,'#936115')
 text(60,310,f'可测算部分合计，非全账户完整利润。第{page+1}/{max(1,math.ceil(len(rows)/12))}张',27,'#66758A')
 group=rows[page*12:(page+1)*12]
 for panel in range(2):
  top=420+panel*740;zero=top+280;scale=250/upper
  for value in [-upper,-upper/2,0,upper/2,upper]:
   y=zero-value*scale;d.line((150,y,1460,y),fill='#172B44' if value==0 else '#E5EAF0',width=3 if value==0 else 1);text(15,y-14,f'{value:,.1f}',22,'#66758A')
  for j,p in enumerate(group[panel*6:panel*6+6]):
   x=260+j*215;v=float(p['profit']);y=zero-v*scale;c='#14975D' if v>0 else '#D83F45' if v<0 else '#66758A'
   d.rectangle((x-45,min(y,zero),x+45,max(y,zero)),fill=c);text(x,y-38 if v>=0 else y+8,f'{v:+,.2f}',29,c,True)
   name=p['name']
   for prefix in ['陈伟霆','CANOTWAIT_','CNW_']:name=name.replace(prefix,'')
   short=name if len(name)<=24 else name[:23]+'…'
   for n in range(0,len(short),6):text(x,zero+285+(n//6)*34,short[n:n+6],25,center=True)
 if not rows:text(750,850,'昨日利润数据待核验，未生成零值柱状图',36,center=True)
 text(60,1930,'退款率'+f'{float(data["policy"]["refund_rate"]):.4%}'+'；成本按预计退款后收入÷'+data['policy']['purchase_multiplier'],28,'#66758A')
 text(60,1980,'未计运费和固定费用；今日可执行追加额度待核验。',29,'#66758A')
 text(60,2030,'无需投手反馈或机器人交互，不自动调整预算。',29,'#66758A')
 im.save(source.parent/f'profit-{data["date"]}-{page+1:02}.png')
