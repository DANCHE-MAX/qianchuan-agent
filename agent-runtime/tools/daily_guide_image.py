"""Render the deterministic daily guide; no network or production configuration reads."""
import json,sys
from pathlib import Path
from decimal import Decimal
from PIL import Image,ImageDraw,ImageFont


def render(view,target,font):
    W=1080;im=Image.new('RGB',(W,2600),'#F1F4F8');d=ImageDraw.Draw(im)
    navy='#172D45';muted='#62748A';red='#CC4D4D';green='#138463';amber='#936115'
    def f(n):return ImageFont.truetype(font,n)
    def text(x,y,s,n=28,c=navy):d.text((x,y),str(s),font=f(n),fill=c)
    def box(x,y,w,h,c='white'):d.rounded_rectangle((x,y,x+w,y+h),24,fill=c)
    def wrap(s,n,width):
        lines=[];line=''
        for ch in str(s):
            if ch=='\n' or d.textlength(line+ch,font=f(n))>width:
                lines.append(line);line='' if ch=='\n' else ch
            else:line+=ch
        if line:lines.append(line)
        return lines
    def para(x,y,s,n=25,c=muted,width=930):
        for line in wrap(s,n,width):text(x,y,line,n,c);y+=n+12
        return y
    def show(v):return '待核验' if v is None else f'{Decimal(str(v)):,.2f}'
    box(0,0,W,205,navy);text(44,26,'CNW / 投放观察',25,'#AFC3D9');text(44,68,'每日三件事',58,'white')
    text(44,150,f'昨日 {view["date"]}  ·  今日核查 {view["today"]}',27,'#D9E4EF')
    box(32,225,1016,368);text(58,247,'01  昨日结果',32)
    text(58,300,'预估净利润',27,muted);p=view['current']['profit'];text(58,350,show(p),62,green if p is not None and Decimal(p)>=0 else red if p is not None else amber)
    text(575,300,'已记录广告费',27,muted);text(575,350,show(view['current']['spend']),54)
    text(58,437,'退款前GMV  '+show(view['current']['gmv'])+'元',28)
    if view.get('coverage'):para(58,492,view['coverage']['sentence'],23,amber)
    else:text(58,492,f'{view["calculable"]}个可计算 / {view["missing"]}个利润待核验 · 合计非完整账户',25,amber)
    box(32,613,1016,390);text(58,636,'02  相比前日的变化',32)
    text(58,690,'金额 / 元',24,muted);text(375,690,view['previous_date'][5:],24,muted);text(590,690,view['date'][5:],24,muted);text(803,690,'变化',24,muted)
    for i,(label,key) in enumerate([('预估净利润','profit'),('广告费','spend'),('退款前GMV','gmv')]):
        y=735+58*i;text(58,y,label,27);text(375,y,show(view['previous'].get(key)),27);text(590,y,show(view['current'].get(key)),27);text(803,y,show(view['changes'][key]),27,red if view['changes'][key] is not None and Decimal(view['changes'][key])<0 else muted)
    note=f'两日可计算计划 {view["previous_calculable"]} / {view["calculable"]} 个；仅比较各日报已知部分。' if view['comparable'] else '前日缺失、账户/日期或口径不符：对比待核验，不用旧日报替代。'
    para(58,924,note,24,amber)
    y=1025;start=y
    # Measure all variable text before drawing the panel.
    height=90
    for item in view['actions']:
        height+=sum(len(wrap(item[k],n,880))*(n+12) for k,n in [('title',29),('detail',25),('action',25)])+38
    box(32,y,1016,height);text(58,y+24,'03  今天先核查这三件事',32);y+=90
    for i,item in enumerate(view['actions']):
        box(58,y,42,44,'#246DD1' if i<2 else '#BD872D');text(70,y+5,str(i+1),27,'white')
        y=para(116,y,item['title'],29,navy,880)
        y=para(116,y+5,item['detail'],25,muted,880)
        y=para(116,y,item['action'],25,muted,880)+33
    y=max(y,start+height)+24
    policy=view['policy'];rate=policy.get('refund_rate');mult=policy.get('purchase_multiplier');fee=policy.get('fee_rate')
    for s in ['金额单位：元。预估净利润为模型投后贡献利润，非财务净利润。',
              '退款率 '+(f'{Decimal(str(rate))*100:.2f}%' if rate is not None else '待核验')+' · 采购倍率 '+str(mult if mult is not None else '待核验')+' · 费率 '+(f'{Decimal(str(fee))*100:.2f}%' if fee is not None else '待核验'),
              '未计运费与固定费用；今日可执行追加额度待核验。',
              '数据截至（北京时间）：昨日 '+view['as_of']+'；前日 '+view['previous_as_of'],
              '仅供人工核查，无需反馈，不自动调整预算。']:
        y=para(44,y,s,22,muted,990)+5
    for warning in view.get('warnings',[]):y=para(44,y,'待核验：'+warning,22,amber,990)+5
    if y+20>im.height:raise ValueError('Guide layout overflow')
    im.crop((0,0,W,y+20)).save(target)

if __name__=='__main__':render(json.loads(Path(sys.argv[1]).read_text()),sys.argv[2],sys.argv[3])
