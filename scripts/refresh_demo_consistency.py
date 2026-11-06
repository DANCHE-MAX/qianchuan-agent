"""Normalize synthetic README figures only; never reads business data or configuration."""
from pathlib import Path
import xml.etree.ElementTree as E
import math,copy
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'docs/assets';NS='http://www.w3.org/2000/svg';E.register_namespace('',NS)
COLORS=['#3478bb','#d38b36','#00a67d','#c43d87'];NAMES=['商品 A','商品 B','商品 C','全店托管']
ADS=[[1500,900,200,400],[1600,1400,300,700]];PROFITS=[[1200,800,-200,None],[1000,1500,-300,None]]

def tag(n):return '{'+NS+'}'+n
def load(f):return E.parse(D/f).getroot()
def save(r,f):E.indent(r);(D/f).write_text(E.tostring(r,encoding='unicode'))
def el(parent,n,**a):return E.SubElement(parent,tag(n),{k.replace('_','-'):str(v) for k,v in a.items()})
def txt(g,x,y,t,z=16,c='#203148',anchor='start'):
 e=el(g,'text',x=x,y=y,font_size=z,fill=c,text_anchor=anchor);e.text=t;return e
def box(g,x,y,w,h,c='#fff',rx=8):el(g,'rect',x=x,y=y,width=w,height=h,fill=c,rx=rx)
def base(h,title):
 r=E.Element(tag('svg'),{'width':'960','height':str(h),'viewBox':f'0 0 960 {h}','role':'img','aria-label':title})
 el(r,'title').text=title;box(r,0,0,960,h,'#f4f7fb',16)
 g=el(r,'g',font_family='-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif',fill='#203148')
 txt(g,30,43,title,25);txt(g,30,76,'统一虚构演示数据 · 非真实报表、截图或发送记录',15,'#9b4b16');return r,g

def pie(g,cx,cy,rad,items):
 angle=-math.pi/2;total=sum(v for i,v in items)
 for i,v in items:
  end=angle+v/total*math.pi*2;a=(cx+rad*math.cos(angle),cy+rad*math.sin(angle));b=(cx+rad*math.cos(end),cy+rad*math.sin(end))
  el(g,'path',d=f'M {cx} {cy} L {a[0]} {a[1]} A {rad} {rad} 0 {int(v/total>.5)} 1 {b[0]} {b[1]} Z',fill=COLORS[i],stroke='#fff',stroke_width=2);angle=end

def percent(v,total):return f'{v/total*100:.1f}'.rstrip('0').rstrip('.')+'%'
# One consistent yesterday profit view.
r,g=base(590,'昨日利润｜哪些商品需要关注？')
for x,title,val in [(30,'广告消耗｜含全店托管','¥4,000'),(340,'可计算部分预计利润','+¥2,200'),(650,'数据范围','3项可算 / 托管待核验')]:
 box(g,x,95,280,78);txt(g,x+15,120,title,14,'#64748b');txt(g,x+15,154,val,22)
txt(g,30,205,'预计利润（元）｜颜色表示商品，上下位置表示盈亏',15,'#64748b')
for v in [1500,1000,500,0,-300]:
 y=390-v*.1;el(g,'line',x1=100,y1=y,x2=910,y2=y,stroke='#9aaaba' if v==0 else '#dce4ef');txt(g,80,y+5,f'{v:,}',13,'#64748b','end')
for i,(x,v) in enumerate(zip([170,360,550,740],PROFITS[1])):
 if v is not None:
  h=abs(v)*.1;y=390-h if v>0 else 390;box(g,x,y,94,h,COLORS[i],3);txt(g,x+47,y-12 if v>0 else y+h+22,f'{v:+,}',20,'#cd5757' if v<0 else '#203148','middle')
 else:txt(g,x+47,330,'待核验',21,'#64748b','middle');txt(g,x+47,358,'收入缺失',14,'#64748b','middle')
 box(g,x+5,467,12,12,COLORS[i],2);txt(g,x+22,478,NAMES[i],16)
txt(g,30,523,'A、B盈利合计 ¥2,500 − C亏损 ¥300 = 可计算部分 ¥2,200。',17)
txt(g,30,557,'全店托管广告费 ¥700 已计入消耗，利润未知；预计利润不是最终财务利润。',14,'#64748b')
save(r,'profit-report-template.svg')
# Same four products and colors for spend and positive-profit shares.
r,g=base(730,'两日对比｜钱花在哪里，盈利贡献来自哪里？')
for x,c,n in zip([30,200,370,540],COLORS,NAMES):box(g,x,96,14,14,c,2);txt(g,x+22,109,n,15)
for day in range(2):
 for col in range(2):
  x=24+col*468;y=130+day*250;box(g,x,y,444,233)
  txt(g,x+18,y+30,['前天','昨天'][day]+' · '+['盈利商品贡献','广告消耗占比'][col],19)
  items=[(i,v) for i,v in enumerate(PROFITS[day] if col==0 else ADS[day]) if v is not None and v>0]
  total=sum(v for i,v in items);pie(g,x+103,y+127,62,items)
  for k,(i,v) in enumerate(items):txt(g,x+195,y+70+k*28,NAMES[i]+'  '+percent(v,total),16)
  txt(g,x+195,y+211,('盈利合计 ' if col==0 else '消耗合计 ')+f'¥{total:,}',16)
txt(g,30,653,'商品 C 亏损另列：前天 −¥200 / 昨天 −¥300，不进入盈利饼图。',16)
txt(g,30,686,'全店托管利润均待核验；左图只看盈利商品，不代表全账户净利润。',14,'#64748b')
save(r,'two-day-contribution-template.svg')
# Split existing total managed spending into product C and remaining managed spending.
r=load('two-day-spend-template.svg');g=r.find(tag('g'))
# Idempotency: original three-color file only; subsequent runs retain checked normalized stack.
if not any(e.get('fill')==COLORS[2] for e in g):
 pink=[e for e in g if e.tag==tag('rect') and e.get('fill')==COLORS[3] and float(e.get('y','0'))>250]
 assert len(pink)==24
 cprev=[0,0,0,0,10,20,20,20,30,30,40,30];cnow=[3,3,3,6,12,18,27,30,36,48,57,57]
 assert sum(cprev)==200 and sum(cnow)==300
 for k,e in enumerate(pink):
  amount=[cprev,cnow][k%2][k//2];h=amount/750*160;y=float(e.get('y'));oldh=float(e.get('height'))
  e.set('height',str(oldh-h));el(g,'rect',x=e.get('x'),y=y+oldh-h,width=e.get('width'),height=h,fill=COLORS[2])
 for e in g:
  if e.tag==tag('rect') and e.get('x')=='330' and e.get('y')=='192':e.set('x','510')
  if e.tag==tag('text') and e.get('x')=='354' and e.get('y')=='206':e.set('x','534')
 box(g,330,192,16,16,COLORS[2],3);txt(g,354,206,'商品 C',15)
for e in g:
 if e.text and '全店托管两日合计' in e.text:e.text='全店托管两日合计 ¥1,100，未拆到单品；商品 C 两日消耗 ¥500。缺数不补零。'
save(r,'two-day-spend-template.svg')
# Feishu embeds the very same figures: cannot silently drift from standalone diagrams.
r,g=base(3010,'在飞书里，会收到什么？')
box(g,24,95,912,2855,'#eaf0f8',14);box(g,24,95,912,70)
txt(g,45,124,'投放协作群 · 演示',21);txt(g,45,150,'千川助手｜日报图表与余额提醒',14,'#64748b')
txt(g,100,199,'演示当天 10:00 · 千川助手',16)
box(g,100,216,792,67);txt(g,120,243,'@投手（演示）  日报已生成，附两日消耗与贡献图。',17,'#3370ff')
txt(g,120,267,'图片看重点，Excel查明细；所有金额均为虚构演示。',14,'#64748b')
def embed(f,y,h):
 source=load(f);source.set('x','100');source.set('y',str(y));source.set('width','792');source.set('height',str(h));source.set('aria-label','飞书附件预览：'+f)
 # Nested inline SVG uses the identical standalone content, no external resources.
 g.append(source)
embed('profit-report-template.svg',297,487)
box(g,100,796,792,65);txt(g,120,825,'Excel｜昨日计划利润明细_演示.xlsx',18);txt(g,120,849,'附件示意 · 明细、计算方法与缺失核查',13,'#64748b')
embed('two-day-spend-template.svg',875,767)
embed('two-day-contribution-template.svg',1656,602)
txt(g,100,2294,'演示当天 12:00 · 千川助手',16)
box(g,100,2307,792,45);txt(g,120,2336,'@投手（演示）  余额速率对比',18,'#3370ff')
embed('balance-comparison-template.svg',2366,429)
txt(g,100,2835,'商品颜色：蓝 A / 橙 B / 绿 C / 玫红全店托管。',15)
txt(g,100,2865,'余额图颜色只代表时段，不代表商品；余额下降不等于广告消耗。',14,'#64748b')
txt(g,30,2990,'展示模板，非真实发送回执；实际发送时刻与内容以授权配置为准。',14,'#64748b')
save(r,'feishu-messages-template.svg')
assert sum(ADS[0])==3000 and sum(ADS[1])==4000
assert sum(v for v in PROFITS[1] if v is not None)==2200
print('统一演示：前天消耗3000，昨天4000；昨天盈利2500、亏损300、可算利润2200；托管未知。')
