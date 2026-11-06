"""Render approved mobile interpretation layout from a local summary JSON."""
import json,sys
from decimal import Decimal
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

def render(summary,output,font):
    im=Image.new('RGB',(900,1200),'#F3F5F7');d=ImageDraw.Draw(im)
    def f(size):return ImageFont.truetype(font,size)
    def text(x,y,value,size=28,color='#182B3B'):d.text((x,y),str(value),font=f(size),fill=color)
    def card(box,color='#FFFFFF'):d.rounded_rectangle(box,radius=24,fill=color)
    def amount(value,signed=False):
        if value is None:return '待核验'
        v=Decimal(value)
        return ('+' if signed and v>0 else '')+format(v,',.2f')
    def fitted(x,y,value,size,width,color):
        while size>24 and d.textbbox((0,0),value,font=f(size))[2]>width:size-=1
        text(x,y,value,size,color)
    def name_lines(value):
        lines=['']
        for ch in value:
            if d.textbbox((0,0),lines[-1]+ch,font=f(27))[2]>720:
                if len(lines)==2:
                    lines[-1]=lines[-1][:-1]+'…';break
                lines.append('')
            lines[-1]+=ch
        return lines
    text(44,38,'CNW  /  日报解读',34)
    text(44,94,'数据日期  '+summary['date']+'  ·  北京时间',24,'#687785')
    card((36,148,864,332),'#172E3E');text(64,176,'一句话结论',24,'#ADBCC6')
    text(64,220,summary['headline'],42,'#FFFFFF');text(64,278,summary['subhead'],28,'#DCE6EC')
    card((36,350,444,540));card((460,350,864,540))
    text(64,378,'预估净利润',28,'#536271')
    p=summary['profit'];color='#687785' if p is None else '#14835A' if Decimal(p)>=0 else '#CC4A4A'
    fitted(64,426,amount(p,True),54,350,color);text(66,498,'元 · 仅可计算部分',22,'#687785')
    text(488,378,'已花广告费',28,'#536271');fitted(488,426,amount(summary['spend']),54,345,'#182B3B')
    text(490,498,'元 · 对应可计算部分',22,'#687785')
    card((36,558,864,638),'#FFF1DB');text(64,582,'%s条可测算  /  %s条利润待核验'%(summary['calculable'],summary['missing']),29,'#896019')
    text(44,672,'只看两个重点' if len(summary['focus'])==2 else '重点计划',29)
    for i,row in enumerate(summary['focus']):
        top=722+i*160;card((36,top,864,top+144))
        positive=Decimal(row['profit'])>=0;accent='#14835A' if positive else '#CC4A4A'
        d.rounded_rectangle((58,top+24,64,top+118),radius=3,fill=accent)
        for j,line in enumerate(name_lines(row['name'])):text(85,top+14+j*32,line,27)
        text(85,top+102,'已花 ¥'+amount(row['spend']),25,'#657483')
        fitted(505,top+102,('预估赚 ¥' if positive else '预估亏 ¥')+amount(str(abs(Decimal(row['profit'])))),26,330,accent)
    if not summary['focus']:text(64,750,'缺少完整数据，不显示虚假盈利或亏损。',28,'#687785')
    text(44,1084,'关注：'+summary['attention'],25)
    text(44,1140,'历史情景估算，未扣固定费用；不是今天的可追加预算。',21,'#77848E')
    output=Path(output);tmp=output.with_suffix('.tmp.png');im.save(tmp);tmp.replace(output)

if __name__=='__main__':render(json.loads(Path(sys.argv[1]).read_text()),sys.argv[2],sys.argv[3])
