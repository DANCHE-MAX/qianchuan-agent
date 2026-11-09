"""Plain-language collection notices; names may be historical, settings never are."""
import json
from pathlib import Path


def previous_names(home, plans):
    wanted={str(p['plan_id']):str(p.get('account_id','')) for p in plans}
    names={}
    for path in sorted((Path(home)/'settings').glob('*.json'),reverse=True)[:48]:
        try: data=json.loads(path.read_text())
        except (OSError,ValueError):continue
        for row in data.get('settings',[]):
            pid=str(row.get('plan_id',''))
            if (pid in wanted and pid not in names and wanted[pid]
                and str(data.get('account_id',''))==wanted[pid] and row.get('status')=='ok' and row.get('name')):
                names[pid]=row['name']
    return names


def lines(report, names=None):
    names=names or {}
    plans={str(p['plan_id']):p for p in report.get('plans',[])}
    result=[]
    for error in report.get('errors',[])[:8]:
        pid=str(error.get('plan_id',''));plan=plans.get(pid,{})
        name=plan.get('name') or names.get(pid)
        label=(name+('（上次采集名称）' if not plan.get('name') else '')) if name else ('计划'+pid if pid else '本轮采集')
        component=error.get('component')
        if component=='commerce_revision':
            text='净成交与日报数据不一致；暂不生成ROI调整建议。'
        elif component=='commerce':
            text='净成交补采失败，净成交数据待核验；暂不能判断退款后的成交质量。'
        elif component=='daily_recovery':
            text='历史日报补采失败，缺失日期待核验；暂不能判断连续表现。'
        elif pid:
            reason='接口限流' if error.get('code')==40100 else '设置读取失败'
            text=reason+'，当前目标ROI等设置待核验；暂不提供该计划的ROI调整建议。'
        else:text='分析未完成，具体缺项待核验；暂不提供ROI调整建议。'
        result.append(label+'：'+text)
    if len(report.get('errors',[]))>8:result.append('另有%d项采集异常待核验。'%(len(report['errors'])-8))
    return result+['缺失未补零；不自动修改ROI或暂停计划，预算仍按原有门禁检查。']
