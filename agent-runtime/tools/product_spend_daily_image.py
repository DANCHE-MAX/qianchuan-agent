"""Pure local rendering of the approved solid-color two-day spend chart."""
from PIL import Image,ImageDraw,ImageFont
from pathlib import Path
from decimal import Decimal as D
import json,math,sys
v=json.loads(Path(sys.argv[1]).read_text());font=sys.argv[3]
im=Image.new('RGB',(1200,2110),'#F3F5F7');d=ImageDraw.Draw(im);navy='#193344';muted='#71818D';colors=['#1769E0','#F2A000','#00A67D','#A7B1BA','#64748B','#C43D87']
def text(x,y,s,size=26,color=navy,anchor=None):d.text((x,y),s,font=ImageFont.truetype(font,size),fill=color,anchor=anchor)
def box(rect,fill='white'):d.rounded_rectangle(rect,radius=22,fill=fill)
def wrapped(s,width,size):
 lines=[];line=''
 for c in s:
  if d.textlength(line+c,font=ImageFont.truetype(font,size))>width:lines.append(line);line=c
  else:line+=c
 if line:lines.append(line)
 return lines
def patch(x1,y1,x2,y2,idx):
 if y2<=y1:return
 d.rectangle((x1,y1,x2,y2),fill=colors[idx])
text(44,32,'商品广告消耗 · 两日对比',43);text(44,94,v['series'][0]['day']+' vs '+v['series'][1]['day']+' ｜12个两小时时段',27,muted)
box((40,145,1160,305),navy)
a,b=[D(x['total']) if x['total'] is not None else None for x in v['series']]
for x,label,value in [(68,v['series'][0]['day'][5:]+' · 商品计划合计',a),(450,v['series'][1]['day'][5:]+' · 商品计划合计',b)]:
 text(x,168,label,25,'#BDCDD6');text(x,219,'¥'+format(value,',.2f') if value is not None else '待核验',44,'white')
text(850,168,'较前一天',25,'#BDCDD6');text(850,219,format((b/a-1)*100,'+.1f')+'%' if a is not None and b is not None and a!=0 else '待核验',44,'#A9DBFF')
box((40,330,1160,690));text(65,351,'两天合计前三名 · 已明确关联商品' if v['complete'] else '部分数据待核验 · 排名仅供参考',29)
for i,pr in enumerate(v['products']):
 y=405+i*65;patch(65,y+4,88,y+27,i)
 lines=wrapped(pr['name'],875,24)
 for j,line in enumerate(lines):text(103,y+j*28,line,24)
 text(1130,y,'¥'+pr['total'],25,colors[i],'ra')
patch(65,617,88,639,3);text(103,613,'其他商品',24)
patch(370,617,393,639,5);text(408,613,'全店托管',24)
patch(670,617,693,639,4);text(708,613,'商品归属待核验',24)
text(65,655,'相同商品始终同色；左柱'+v['series'][0]['day'][5:]+'，右柱'+v['series'][1]['day'][5:]+'。',23,muted)
peak=max((sum(float(z) for z in b) for s in v['series'] for b in s['slots'] if b is not None),default=0);top=max(50,math.ceil(peak/50)*50)
for panel in range(2):
 y=715+panel*550;box((40,y,1160,y+525));text(65,y+22,'00:00—12:00' if panel==0 else '12:00—24:00',30);text(1125,y+27,'单位：元',23,muted,'ra')
 left,right=125,1125;base=y+425;height=320
 for tick in range(4):
  val=top*tick/3;yy=base-height*tick/3;d.line((left,yy,right,yy),fill='#E3E9EE',width=2);text(left-18,yy,format(val,'.0f'),23,muted,'rm')
 for k in range(6):
  i=panel*6+k;cx=left+(k+.5)*(right-left)/6
  for day in range(2):
   x=cx+(-36 if day==0 else 36);base_y=base
   slot=v['series'][day]['slots'][i]
   if slot is None:
    text(x,base-12,'待核验',19,muted,'mb');text(x,base+12,v['series'][day]['day'][-2:],20,muted,'mt');continue
   bins=list(map(float,slot))
   for idx,val in enumerate(bins):
    h=height*val/top
    if h>0:patch(int(x-25),base_y-h,int(x+25),base_y,idx)
    base_y-=h
   text(x,base_y-10,format(sum(bins),'.2f'),23,navy,'mb');text(x,base+12,v['series'][day]['day'][-2:],20,muted,'mt')
  text(cx,base+49,f'{i*2:02d}—{(i+1)*2:02d}',25,navy,'mt')
text(45,1840,'全店托管：'+(('两天合计' if v['complete'] else '已核验部分')+' ¥'+v.get('managed','0') if v.get('managed') is not None else '待核验'),30,'#995F5A')
text(45,1890,'其他未关联计划消耗：'+('¥'+v['unknown'] if v['unknown'] is not None else '待核验')+'；不计入全店托管。',24,muted)
text(45,1930,('已确认退款差异仅影响GMV口径；广告消耗已独立核验。' if any('业务确认退款' in w for w in v['warnings']) else '小时明细与两天商品计划汇总一致。') if v['complete'] else '部分明细或对账待核验，不显示为零；暂不比较全天合计。',24,muted)
text(45,1970,'口径：千川实际广告消耗，不使用余额差；两块图统一零起点及刻度。',24,muted)
text(45,2010,'直播消耗覆盖待核验，未纳入本图。' if any('直播' in w for w in v['warnings']) else '仅比较商品计划广告消耗。',24,muted)
text(45,2050,'商品关联采用采集时计划信息 · 不自动调整预算或投放状态',23,muted)
im.save(sys.argv[2])
