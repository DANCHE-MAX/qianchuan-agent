"""Compact current-data replies; detailed checks remain available on request."""
import re
import json
from datetime import datetime
from decimal import Decimal
from . import hourly as h, analysis_gate

VERSION=27


def detailed(question):
    return bool(re.search(r'详细|展开|明细|逐计划|全部计划|完整数据|完整报告|数据口径|诊断详情|采集详情|健康检查|运行状态|数据检查|完整性|缺口|缺(?:了|少)?多少|采集进度',question))


def cutoff(info):
    value=info.get('数据截止时间') or (info.get('异常分析准入') or {}).get('cutoff')
    if not value and info.get('资料类型')=='今日小时采集':
        covered=info.get('覆盖小时') or []
        if covered:value=max(row['end'] for row in covered)
    value=value or info.get('最新采集时间')
    try:
        at=datetime.fromisoformat(value)
        if at.tzinfo is None:raise ValueError()
        return at.astimezone(h.CHINA).strftime('%m月%d日截至%H:%M')
    except (ValueError,TypeError):return '采集截止时间待核对'


def limitation(info):
    admission=info.get('异常分析准入') or {};state=admission.get('state');reasons=admission.get('reasons',[])
    if state=='empty':return '本次接口未返回经营记录，暂不能确认投放表现。'
    if 'stale' in reasons:return '快照已过期，仅供截至该时点参考，暂不能判断当前趋势。'
    if state=='partial':return '部分计划或指标的区间数据受限，相关趋势暂无法判断。'
    if 'gap' in reasons:return '半小时样本暂不连续，尚不能判断加速或异常趋势。'
    if 'samples' in reasons:return '半小时样本不足，尚不能判断加速或异常趋势。'
    if state in ('waiting','blocked'):return '区间数据暂不满足分析条件，尚不能判断异常趋势。'
    quality=info.get('数据完整性检查') or {}
    if (quality.get('components') or {}).get('snapshot')=='stale':return '快照已过期，仅供截至该时点参考。'
    if info.get('资料类型')=='今日小时采集' and info.get('缺失小时'):
        return '小时数据存在缺口，当前汇总并不完整。'
    return ''


def money(value):
    if value is None:return '暂无'
    number=Decimal(str(value))
    if not number.is_finite() or number<0:raise ValueError('金额或ROI无效')
    return format(number,',.2f')


def stats(info,keys=('ad_spend','gmv','payment_roi')):
    totals=info.get('整体') or {}
    labels={'ad_spend':'消耗','gmv':'成交','payment_roi':'整体支付ROI'}
    result=[]
    for key in keys:
        value=totals.get(key)
        # Derive the primary ROI from verified gross GMV/spend, never net/cost ROI.
        if key=='payment_roi':
            spend=totals.get('ad_spend');gmv=totals.get('gmv')
            value=Decimal(str(gmv))/Decimal(str(spend)) if spend is not None and gmv is not None and Decimal(str(spend))>0 else None
        result.append(labels[key]+money(value)+('元' if value is not None and key!='payment_roi' else ''))
    return '，'.join(result)


def scope_label(info):
    evidence=json.dumps(info.get('统计范围') or {},ensure_ascii=False)
    return '乘方计划' if '乘方' in evidence else ''


def direct(question,info):
    """Only unambiguous account-wide queries bypass LLM; names/IDs keep retrieval."""
    if detailed(question):return None
    question=re.sub(r'\s+','',question).strip('。？！!?')
    match=re.fullmatch(r'(?:请)?(?:帮我|为我|给我)?(?:查询|查看|总结)?(?:当前|目前|现在|今日|今天)(?:的)?(?:总|整体|累计)?'
                      r'(消耗|花费|成交金额|成交|GMV|整体支付ROI|支付ROI|ROI|情况|表现|简报|总结|数据|概况|投放情况|概览)'
                      r'(?:是多少|为多少|有多少|多少|怎么样|如何)?',question,re.I)
    if not match:return None
    head=cutoff(info);note=limitation(info)
    if (info.get('异常分析准入') or {}).get('state')=='empty':return head+'，'+note
    term=match[1].upper()
    keys=('ad_spend',) if term in ('消耗','花费') else ('gmv',) if term in ('成交','成交金额','GMV') else ('payment_roi',) if 'ROI' in term else ('ad_spend','gmv','payment_roi')
    return head+'，'+scope_label(info)+stats(info,keys)+'。'+note


def clean_generated(text):
    # Remove only the application's fixed current-data preamble.
    prefixes=('依据累计快照及监测资料（以采集时间和范围为准）。',
              '依据今日已采集完整小时（不含正在进行的小时）及检索资料。')
    text=text.strip()
    for prefix in prefixes:
        if text.startswith(prefix):text=text[len(prefix):].lstrip()
    # Current-data-only citations have one source; preserve SOP/multiple-source attribution.
    parts=re.split(r'\n\s*参考资料：\s*\n',text,maxsplit=1)
    if len(parts)==2:
        body,sources=parts
        lines=[line.strip() for line in sources.splitlines() if line.strip()]
        if lines==['[1] 今日半小时采集'] or lines==['[1] 今日小时采集']:
            text=re.sub(r'\[1\]','',body).strip()
        else:text=body.strip()+'\n来源：'+'；'.join(lines)
    return text


def render_generated(text,info):
    # Keep the v26 all-clear guard, but use a concise numeric fallback if it fires.
    guarded=analysis_gate.safe_answer(text,info)
    if guarded!=text:return cutoff(info)+'，'+scope_label(info)+stats(info)+'。'+limitation(info)
    text=clean_generated(text)
    if not re.search(r'\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}',text):text=cutoff(info)+'。'+text
    note=limitation(info)
    if note and not re.search(r'暂(?:时)?(?:无|没|不)|不能判断|无法判断|不连续|样本不足|受限|过期|缺口',text):
        text+='\n'+note
    return text
