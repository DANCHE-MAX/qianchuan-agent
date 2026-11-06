"""Build README-only figures from fictional constants; never read runtime data/config."""
from pathlib import Path
import xml.etree.ElementTree as E
import math,copy
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'docs/assets';NS='http://www.w3.org/2000/svg';E.register_namespace('',NS)
NAMES=['商品 A','商品 B','商品 C','全店托管','商品归属待核验']
COLORS=['#3478bb','#d38b36','#00a67d','#c43d87','#64748b']
ADS=[[1500,900,200,300,100],[1600,1400,300,500,200]]
PROFITS=[[1200,800,-200,100,-50],[1000,1500,-300,-100,200]]
def tag(n):return '{'+NS+'}'+n
def el(g,n,**a):return E.SubElement(g,tag(n),{k.replace('_','-'):str(v) for k,v in a.items()})
def txt(g,x,y,s,z=18,c='#203148',anchor='start'):
 e=el(g,'text',x=x,y=y,font_size=z,fill=c,text_anchor=anchor);e.text=str(s)
def box(g,x,y,w,h,c='white',rx=12):el(g,'rect',x=x,y=y,width=w,height=h,fill=c,rx=rx)
def base(h,title):
 r=E.Element(tag('svg'),width='960',height=str(h),viewBox=f'0 0 960 {h}',role='img',attrib={'aria-label':title})
 el(r,'title').text=title;box(r,0,0,960,h,'#f4f7fb');g=el(r,'g',font_family='PingFang SC,Microsoft YaHei,Arial,sans-serif')
 txt(g,30,43,title,28);txt(g,30,78,'全部为虚构演示数据 · 非真实报表、截图或发送记录',17,'#986320');return r,g
ASSETS={}
def save(r,name):
 E.indent(r);(D/name).write_text(E.tostring(r,encoding='unicode'),encoding='utf-8');ASSETS[name]=r

def pie(g,x,y,items,r=70):
 total=sum(v for _,v in items);start=-math.pi/2
 for i,v in items:
  end=start+v/total*2*math.pi
  el(g,'path',d=f'M {x} {y} L {x+r*math.cos(start)} {y+r*math.sin(start)} A {r} {r} 0 {int(v/total>.5)} 1 {x+r*math.cos(end)} {y+r*math.sin(end)} Z',fill=COLORS[i],stroke='white',stroke_width=2)
  start=end

# First attachment: concise guide with the same figures as every other attachment.
r,g=base(840,'每日三件事｜昨日结果、变化与核查')
box(g,25,100,910,145);txt(g,48,135,'01 昨日结果',24)
for x,label,value in [(48,'可计算部分预估利润','+¥2,300'),(370,'已知广告消耗','¥4,000'),(670,'计划覆盖','5项可算 / 1项待核验')]:
 txt(g,x,173,label,17,'#64748b');txt(g,x,215,value,26)
box(g,25,263,910,205);txt(g,48,301,'02 相比前日',24)
for x,label in [(48,'指标'),(390,'前天'),(580,'昨天'),(770,'变化')]:txt(g,x,338,label,18,'#64748b')
for y,label,a,b in [(382,'预估利润',1850,2300),(428,'已知广告消耗',3000,4000)]:
 txt(g,48,y,label);txt(g,390,y,f'{a:,}');txt(g,580,y,f'{b:,}');txt(g,770,y,f'{b-a:+,}')
box(g,25,485,910,275);txt(g,48,524,'03 今天先核查这三件事',24)
for y,title,detail in [(568,'1 商品 A：核查利润下降','利润 1,200 → 1,000元，减少200元；原因待核验。'),(637,'2 商品 C：核查亏损','昨天预估亏损300元；核查成交、费用和投放时段。'),(706,'3 计划 D：补齐GMV','广告费已核实为0，GMV及利润待核验，不能补0。')]:
 txt(g,48,y,title,20);txt(g,48,y+29,detail,17,'#64748b')
txt(g,30,800,'模型利润不代表完整账户财务净利润；不自动调整预算，无需投手反馈。',17,'#64748b')
save(r,'daily-guide-template.svg')

r,g=base(680,'昨日利润｜全店托管与未关联计划分开')
txt(g,30,125,'已知广告费 ¥4,000    可计算部分预估利润 +¥2,300    计划D利润待核验',20)
for v in [1500,1000,500,0,-300]:
 y=405-v*.12;el(g,'line',x1=80,y1=y,x2=925,y2=y,stroke='#243951' if v==0 else '#dce4ef',stroke_width=2 if v==0 else 1);txt(g,68,y+5,v,14,'#64748b','end')
for i,v in enumerate(PROFITS[1]):
 x=105+i*166;h=abs(v)*.12;y=405-h if v>0 else 405
 box(g,x,y,88,h,'#149663' if v>0 else '#d54d55',3);txt(g,x+44,y-12 if v>0 else y+h+24,f'{v:+,}',22,'#149663' if v>0 else '#d54d55','middle')
 label=NAMES[i] if i<4 else '其他未关联计划';txt(g,x+44,505,label,17,anchor='middle')
txt(g,30,555,'2,500（盈利商品）−300（亏损商品）−100（托管）+200（未关联）= 2,300元',18)
txt(g,30,600,'全店托管是明确标注的计划；未关联计划金额可算，商品归属仍待核验。',17,'#64748b')
txt(g,30,638,'计划D：已核实广告费0；GMV及利润未知，未画成零利润。',17,'#986320')
save(r,'profit-report-template.svg')

# Every two-hour bucket adds exactly to the same daily totals.
def buckets(total):
 vals=[total*i//78 for i in range(1,13)];vals[-1]+=total-sum(vals);assert sum(vals)==total;return vals
bins=[[buckets(v) for v in day] for day in ADS]
r,g=base(920,'商品广告消耗｜前天与昨天的12个时段')
txt(g,30,124,'前天 ¥3,000   →   昨天 ¥4,000     变化 +33.3%',25)
for i in range(5):
 x=30+i*185;box(g,x,160,14,14,COLORS[i],2);txt(g,x+20,173,NAMES[i],15)
for panel in range(2):
 y=210+panel*300;box(g,25,y,910,280);txt(g,45,y+30,'00:00—12:00' if panel==0 else '12:00—24:00',22)
 basey=y+215;scale=180/700
 for v in [0,350,700]:
  yy=basey-v*scale;el(g,'line',x1=85,y1=yy,x2=915,y2=yy,stroke='#dce4ef');txt(g,74,yy+4,v,13,'#64748b','end')
 for k in range(6):
  slot=panel*6+k;cx=145+k*138
  for day in range(2):
   x=cx+(-22 if day==0 else 22);bottom=basey
   for i in range(5):
    h=bins[day][i][slot]*scale;box(g,x-17,bottom-h,34,h,COLORS[i],0);bottom-=h
   txt(g,x,basey+19,'前' if day==0 else '昨',13,'#64748b','middle')
  txt(g,cx,basey+45,f'{slot*2:02d}—{slot*2+2:02d}',16,anchor='middle')
txt(g,30,843,'全店托管两日消耗 ¥800；其他未关联计划 ¥300。两类分开，不强行分摊。',18)
txt(g,30,883,'各时段相加等于日报消耗；商品归属未知不等于金额未知，缺失不补0。',17,'#64748b')
save(r,'two-day-spend-template.svg')

r,g=base(990,'两日四宫格｜盈利贡献与广告消耗')
for day in range(2):
 for col in range(2):
  x=25+col*465;y=105+day*300;box(g,x,y,445,280);txt(g,x+18,y+32,['前天','昨天'][day]+' · '+['盈利商品贡献','广告消耗占比'][col],22)
  items=[(i,v) for i,v in enumerate(PROFITS[day] if col==0 else ADS[day]) if v>0 and (col==1 or i<3)]
  pie(g,x+102,y+142,items)
  for k,(i,v) in enumerate(items):
   txt(g,x+195,y+70+k*30,NAMES[i]+f' {v/sum(z for _,z in items)*100:.1f}%',16)
  txt(g,x+18,y+253,('盈利商品合计 ' if col==0 else '已知消耗合计 ')+f'¥{sum(v for _,v in items):,}',19)
box(g,25,724,910,205);txt(g,45,760,'利润单列，不计入盈利商品饼图',23)
for x,day in [(45,0),(495,1)]:
 txt(g,x,797,['前天','昨天'][day]+f'：商品C {PROFITS[day][2]:+,}元',18)
 txt(g,x,832,f'全店托管 {PROFITS[day][3]:+,}元；其他未关联 {PROFITS[day][4]:+,}元',18)
 txt(g,x,867,f'可计算利润合计 {sum(PROFITS[day]):+,}元',18)
 txt(g,x,901,'计划D：GMV及利润待核验，未按0计算。',16,'#986320')
txt(g,30,967,'盈利饼图只含已关联盈利商品；其余类别单列，相加还原日报可计算利润。',17,'#64748b')
save(r,'two-day-contribution-template.svg')

# Receive-flow mockup embeds the identical SVGs, with a single mention for this daily bundle.
r,g=base(3100,'飞书接收顺序｜整份日报只@一次')
txt(g,30,125,'10:00 · @投手（虚构示意）【每日投放简报】图表与Excel如下，缺失标待核验。',19,'#3370ff')
y=155
for filename in ['daily-guide-template.svg','profit-report-template.svg','excel','two-day-spend-template.svg','two-day-contribution-template.svg']:
 if filename=='excel':
  box(g,50,y,860,85);txt(g,72,y+33,'Excel附件｜昨日计划利润明细_演示.xlsx',22);txt(g,72,y+65,'附件示意：逐计划金额、分类、计算口径与缺失项。',17,'#64748b');y+=105;continue
 source=copy.deepcopy(ASSETS[filename]);h=float(source.get('height'))*860/960
 source.set('x','50');source.set('y',str(y));source.set('width','860');source.set('height',str(h));g.append(source);y+=h+20
txt(g,30,y+22,'以上为同一个日报包；余额提醒按独立时段发送，未包含在此流程中。',17,'#64748b')
height=int(y+60);r.set('height',str(height));r.set('viewBox',f'0 0 960 {height}');r.find(tag('rect')).set('height',str(height))
save(r,'feishu-messages-template.svg')
assert [sum(x) for x in ADS]==[3000,4000]
assert [sum(x) for x in PROFITS]==[1850,2300]
print('Fictional figures verified: spend 3000/4000; calculable profit 1850/2300; categories distinct.')
