"""Read the exact daily profit bundle, never substitute a legacy/latest report."""
import json
import re
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
DATE = re.compile(r'(?<!\d)(?:(\d{4})[-/.年])?(\d{1,2})[-/.月](\d{1,2})(?:日|号)?(?!\d)')

def enabled(outdir):
    path = Path(outdir).parent / 'config/daily_brief.json'
    if not path.exists():
        return False
    try:
        return json.loads(path.read_text()).get('profit_report') is True
    except (OSError, ValueError, AttributeError):
        # Malformed configuration must not silently select a legacy report.
        return True

def target_day(command, now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError('日期校验需要带时区的时间')
    today = now.astimezone(TZ).date()
    dates = set()
    for year, month, day in DATE.findall(command):
        try:
            dates.add(date(int(year or today.year), int(month), int(day)))
        except ValueError:
            raise ValueError('日期无效，请使用YYYY-MM-DD') from None
    for words, offset in [(('昨日', '昨天'), 1), (('前日', '前天'), 2), (('今日', '今天'), 0)]:
        if any(word in command for word in words):
            dates.add(today - timedelta(days=offset))
    if len(dates) > 1 or any(w in command for w in ('这几天', '六日', '六天', '近一周', '最近一周')):
        raise ValueError('请指定一个完整自然日，多日范围不能用单日日报代替')
    result = next(iter(dates), today - timedelta(days=1))
    if result >= today:
        raise ValueError('新版日报仅分析已结束的北京时间完整自然日')
    return result.isoformat()

def load(outdir, account, day):
    if not account or not str(account).isdigit():
        raise ValueError('账户未明确')
    root = Path(outdir).resolve()
    dbpath = root.parent / 'data/qianchuan/daily_brief.db'
    rows = []
    if dbpath.exists():
        with sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT sent,payload FROM jobs WHERE account=? AND day=?', (str(account), day)).fetchall()
    if not rows or len(rows) == 1 and rows[0][0] != 1:
        from .manual_profit import completed
        return completed(outdir, account, day)
    if len(rows) != 1:
        raise ValueError('日报记录冲突')
    bundle = json.loads(rows[0][1])
    directory = Path(bundle['directory']).resolve()
    expected = root / 'profit_daily' / str(account) / day
    if not directory.is_relative_to(expected) or bundle['date'] != day:
        raise ValueError('日报来源日期不匹配')
    result = json.loads((directory / 'result.json').read_text())
    if (result.get('date') != day or str(result.get('account')) != str(account)
            or result.get('version') != 'qianchuan-plan-scenario-v1'
            or not isinstance(result.get('rows'), list)
            or not isinstance(result.get('totals_calculable_only'), dict)):
        raise ValueError('日报数据无效')
    return result

def answer(command, outdir, env, kb, *, summary_only=False):
    try:
        day = target_day(command)
    except ValueError as exc:
        return '待核验：%s。未使用旧日报替代。' % exc
    kb.last_context = json.dumps({'资料类型': '新版利润日报', '数据日期': day, '状态': '待核验'}, ensure_ascii=False)
    try:
        report = load(outdir, (env or {}).get('QC_ADVERTISER_IDS', '').strip(), day)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return '数据日期：%s（北京时间）。该日新版日报尚未完成或无法校验，数据待核验；未使用旧日报、其他日期或其他账户数据替代。' % day
    context = json.dumps({
        '资料类型': '新版利润日报', '数据日期': day, '时区': 'Asia/Shanghai',
        '报告': report,
        '字段解释': {'profit': '扣实际广告费后的预估净利润，未扣人员租金等固定费用',
                   'spend': '实际广告费', 'profit15': '按退款前GMV的15%广告费情景利润',
                   'candidate': '历史静态候选追加空间，不是今天可执行预算',
                   'totals_calculable_only': '仅可计算计划合计，缺失计划不能视为0，不代表完整账户利润'},
        '限制': '严格使用本报告日期、政策和原始金额；null和缺失均为待核验，不能补0。按字段分别判断：已确认的spend=0就是零消耗，不能因为GMV或利润缺失就说消耗未知；只有spend本身缺失才表示消耗待核验。缺失计划不得推断可投额度。SOP仅作参考，不能覆盖报告政策或补造价格档。不能称为公司最终净利润。不得把历史空间当成今天预算。反馈暂停，不要求投手反馈，不执行预算、ROI或计划状态调整。'
    }, ensure_ascii=False)
    kb.report_snapshot = None
    if summary_only:
        from .profit_summary import view
        summary = view(report)
        kb.last_context = context
        return '数据日期：%s（北京时间）。%s；预估利润%s元，已花广告费%s元；%s。' % (
            day, summary['headline'], summary['profit'] if summary['profit'] is not None else '待核验',
            summary['spend'] if summary['spend'] is not None else '待核验', summary['subhead'])
    if not env or env.get('KNOWLEDGE_ENABLED', '').lower() != 'true':
        return '数据日期：%s（北京时间）。知识问答尚未开启。' % day
    result = kb.ask(command + '\n仅分析数据日期' + day + '的新版利润日报；缺失明确列为待核验。', env, report_context=context)
    # Persist exact source for safe follow-ups, never the model's answer as evidence.
    if kb.last_context:
        kb.last_context = context
    return '数据日期：%s（北京时间，新版利润日报）。\n' % day + result

def followup_command(command, previous):
    if DATE.search(command) or any(w in command for w in ('昨日','昨天','前天','前日','今日','今天')):
        return command
    try:
        source = json.loads(previous.get('source', ''))
        if source.get('资料类型') == '新版利润日报':
            date.fromisoformat(source['数据日期'])
            return command + '；分析' + source['数据日期'] + '日报'
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    return command  # Old conversation is discarded; default to Beijing yesterday.
