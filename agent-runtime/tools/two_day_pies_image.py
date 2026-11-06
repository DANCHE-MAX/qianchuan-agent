"""Four pies, separate losses and complete product-name legend. Local deterministic render."""
import json,math,sys
from decimal import Decimal
from PIL import Image,ImageDraw,ImageFont
from pathlib import Path


def render(view,path,font):
    width=1400;ink='#193344';muted='#71818D';red='#B64545'
    legend=view['legend'];colors={g['id']:g['color'] for g in legend}
    # Size for full names and all losses, no hidden rows or fixed-height clipping.
    loss_count=max((len(d['losses']) for d in view['days']),default=0)
    loss_height=max(520,190+loss_count*56+200)
    legend_height=90+sum(45+len(g['name'])//48*28 for g in legend)
    height=160+2*720+30+loss_height+40+legend_height+180
    image=Image.new('RGB',(width,height),'#F3F5F7');draw=ImageDraw.Draw(image)
    def f(z):return ImageFont.truetype(font,z)
    def text(x,y,s,z=26,c=ink,anchor=None):draw.text((x,y),s,font=f(z),fill=c,anchor=anchor)
    def wrap(s,w,z):
        result=[];line=''
        for c in s:
            if draw.textlength(line+c,font=f(z))>w:result.append(line);line=c
            else:line+=c
        if line:result.append(line)
        return result
    def label(name):
        for prefix in ('陈伟霆','CANOTWAIT_','CNW_'):name=name.replace(prefix,'')
        return name[:21]+'…' if len(name)>21 else name
    def money(value):return '待核验' if value is None else '¥'+format(Decimal(value),',.2f')
    def box(rect):draw.rounded_rectangle(rect,radius=24,fill='white')
    text(45,32,'商品贡献占比 · 两日四宫格',46)
    text(45,100,' / '.join(d['day'] for d in view['days'])+' · 北京时间完整自然日',28,muted)
    for row,day in enumerate(view['days']):
        y=160+row*720
        for col,key in enumerate(('positive','ads')):
            x=40+col*690;box((x,y,x+650,y+690))
            text(x+25,y+22,day['day'][5:]+'｜'+('盈利商品贡献占比' if col==0 else '广告消耗占比'),29)
            text(x+25,y+68,('已测算部分 · 预估利润' if col==0 else '商品计划 · 实际广告消耗') if day['complete'] else '数据或对账待核验',23,muted)
            cx=x+325;cy=y+255;radius=145;start=-90;items=day[key]
            for item in items:
                angle=float(item['share'])*3.6
                draw.pieslice((cx-radius,cy-radius,cx+radius,cy+radius),start=start,end=start+angle,fill=colors[item['id']],outline='white',width=2)
                if angle>=18:
                    mid=math.radians(start+angle/2)
                    text(cx+math.cos(mid)*99,cy+math.sin(mid)*99,format(Decimal(item['share']),'.1f')+'%',24,'white','mm')
                start+=angle
            if not items:text(cx,cy,'待核验' if not day['complete'] else '无盈利商品' if col==0 else '已核实零消耗',29,muted,'mm')
            text(x+25,y+420,('盈利合计 ' if col==0 else '已取消耗 ')+money(day['positive_total'] if col==0 else day['ad_total']),29)
            for k,item in enumerate(items[:4]):
                yy=y+468+k*43;draw.rectangle((x+25,yy+5,x+41,yy+21),fill=colors[item['id']]);text(x+52,yy,label(item['name']),21);text(x+625,yy,money(item['amount']),22,ink,'ra')
            if len(items)>4:text(x+25,y+655,'其余商品见全名图例；小额扇区未标百分比。',22,muted)
    y=1630;box((40,y,1360,y+loss_height));text(65,y+20,'亏损商品单列 · 不进入盈利贡献饼图',32,red)
    for col,day in enumerate(view['days']):
        x=65+col*680;text(x,y+75,day['day'][5:]+'｜亏损合计 '+money(day['loss_total']),28,red)
        for k,item in enumerate(day['losses']):
            yy=y+125+k*56
            for j,line in enumerate(wrap(label(item['name']),445,23)):text(x,yy+j*26,line,23)
            text(x+600,yy,money(item['amount']),24,red,'ra')
        if not day['losses']:text(x,y+125,'暂无可测算亏损' if day['complete'] else '待核验',25,muted)
        yy=y+loss_height-195
        text(x,yy,'全店托管预估利润：'+(money(day.get('managed_profit')) if day.get('managed_present') or not day['complete'] else '无对应记录'),24)
        text(x,yy+40,'其他未关联计划利润：'+(money(day['unknown_profit']) if day['unknown_present'] or not day['complete'] else '无对应记录'),23,muted)
        text(x,yy+80,'两类均单列，不进入商品盈利贡献分母。',23,muted)
        text(x,yy+120,(str(day['missing_plans'])+'个计划缺完整利润明细，未按0计算。') if day['missing_plans'] is not None else '缺失计划范围待核验，未按0计算。',23,muted)
    y+=loss_height+40;text(45,y,'商品全名图例 · 两天四图同一商品同色',31);y+=55
    for item in legend:
        draw.rectangle((48,y+4,68,y+24),fill=item['color']);lines=wrap(item['name'],1250,24)
        for j,line in enumerate(lines):text(85,y+j*28,line,24)
        y+=max(42,len(lines)*28+10)
    text(45,y+20,'利润为现行退款率/成本口径的情景估算，已扣实际广告费，未扣运费和固定费用。',24,muted)
    text(45,y+60,'盈利占比分母仅含已关联且可测算的盈利商品；亏损、全店托管、未关联计划及缺失另列。',24,muted)
    text(45,y+100,'全店托管仅含明确标注的计划；其他未关联计划不混入，直播覆盖待核验。',24,muted)
    image.crop((0,0,width,y+150)).save(path)

if __name__=='__main__':render(json.loads(Path(sys.argv[1]).read_text()),sys.argv[2],sys.argv[3])
