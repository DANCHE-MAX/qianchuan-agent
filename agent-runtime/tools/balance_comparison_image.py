"""Mobile balance comparison renderer; data and font paths are local inputs."""
import json,math,sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime
from PIL import Image,ImageDraw,ImageFont

def render(v,path,font):
    rows=v['rows'];im=Image.new('RGB',(1000,1170),'#F3F5F7');d=ImageDraw.Draw(im)
    def f(size):return ImageFont.truetype(font,size)
    def t(x,y,text,size=28,color='#1B2D3D'):d.text((x,y),text,font=f(size),fill=color)
    def box(rect,color='#FFFFFF'):d.rounded_rectangle(rect,radius=22,fill=color)
    def fitted(x,y,text,size,width,color):
        while size>19 and d.textbbox((0,0),text,font=f(size))[2]>width:size-=1
        t(x,y,text,size,color)
    def money(value):return '待核验' if value is None else format(Decimal(value),',.2f')
    t(44,38,'余额消耗速率对比',38)
    t(44,102,datetime.fromisoformat(v['end']).strftime('%Y年%m月%d日 %H:%M')+'  ·  北京时间',25,'#74808A')
    box((36,157,964,293),'#172E3E');t(62,180,'本次采样可用余额',24,'#ADC0CC')
    fitted(62,222,('¥' if v['available'] is not None else '')+money(v['available']),40,455,'#FFFFFF')
    t(570,180,'按本期速率估算',24,'#ADC0CC')
    runway=v.get('runway');duration='待核验' if runway is None else '约可用 '+format(Decimal(runway),'.1f')+' 小时'
    fitted(570,226,duration,32,370,'#FFFFFF')
    box((36,319,964,856));t(63,344,'三段速率，同一比例尺',27);t(737,350,'单位：元 / 小时',22,'#74808A')
    rates=[float(r['rate']) for r in rows if r['rate'] is not None]
    peak=max(rates,default=0);scale=10**math.floor(math.log10(peak)) if peak>0 else 1
    maxx=math.ceil(peak/scale)*scale if peak>0 else 1
    left=275;right=830
    for i in range(5):
        value=maxx*i/4;x=left+(right-left)*i/4
        d.line((x,400,x,765),fill='#E8EDF1',width=2)
        t(int(x)-10,784,format(value,'.3g'),22,'#87929A')
    colors=['#2878D4','#E29A30','#8B64CE']
    for i,(row,color) in enumerate(zip(rows,colors)):
        y=421+i*118;t(63,y,row['label'],29,color)
        start=row['window'].split(' ',1);t(63,y+42,start[0],20,'#7B8790');t(63,y+68,start[1],20,'#7B8790')
        if row['rate'] is None:
            t(left+20,y+22,'待核验',28,'#8A959D')
            continue
        value=Decimal(row['rate']);length=(right-left)*float(value)/maxx
        if value>0:d.rounded_rectangle((left,y+13,left+max(2,length),y+59),radius=min(9,max(1,length/2)),fill=color)
        else:d.line((left,y+13,left,y+59),fill=color,width=3)
        label=format(value,'.2f') if value==0 or value>=Decimal('.01') else format(value,'.3g')
        fitted(int(left+length+12),y+19,label,25,110,color)
    t(63,826,'条越长，余额净下降越快；待核验不画成零。',20,'#87929A')
    for i,(key,label) in enumerate([('previous_change','较前两小时'),('yesterday_change','较昨日同时段')]):
        x=36+i*475;box((x,879,x+453,1005));t(x+26,901,label,25,'#75818C')
        value=v[key]
        if value is None:
            text='不适用（基准为0）' if rows[0]['rate'] is not None and rows[i+1]['rate'] is not None and Decimal(rows[i+1]['rate'])==0 else '待核验'
        else:text=('+' if Decimal(value)>0 else '')+format(Decimal(value),'.1f')+'%'
        fitted(x+26,944,text,36,400,colors[i+1])
    if v['available'] is not None and (Decimal(v['available'])<=500 or runway is not None and Decimal(runway)<=2):
        insight='余额或预计可用时长偏低，请及时充值。'
    elif any(r['rate'] is None for r in rows):insight='部分时段待核验，暂不作完整比较。'
    elif all(v[k] is not None and Decimal(v[k])>0 for k in ('previous_change','yesterday_change')):insight='本期余额下降更快，请结合后续消耗留意余额。'
    elif all(v[k] is not None and Decimal(v[k])<0 for k in ('previous_change','yesterday_change')):insight='本期余额下降较慢，继续观察后续消耗。'
    else:insight='按图中两个参照时段分别比较，留意后续消耗。'
    t(44,1037,insight,27)
    t(44,1090,'口径：余额净下降观测速率，不等于已核算广告消耗。',22,'#7A8791')
    t(44,1126,'邻近采样按实际间隔计算；缺数或资金变动标待核验。',21,'#7A8791')
    path=Path(path);tmp=path.with_suffix('.tmp.png');im.save(tmp);tmp.replace(path)

if __name__=='__main__':render(json.loads(Path(sys.argv[1]).read_text()),sys.argv[2],sys.argv[3])
