"""Read-only current Chuanfang product-plan budget diagnostics.

List API is metadata only. Current performance comes from the yuan report.
Target-to-reported-ROI mapping remains unconfirmed; no platform writes.
Official list: 9000000000000003; detail: 1804362305657868.
"""
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import budgets, qc_auth, budget_report
from .card_view import card, fields, text_element
from .conversation import Conversation
from .notify import feishu_allowed_chat_ids
from .reliability import ServiceError

CHINA = timezone(timedelta(hours=8))
LIST_URL = 'https://api.oceanengine.com/open_api/v1.0/qianchuan/uni_promotion/list/'
VERSION=32
STATES = {'DELIVERY_OK':'投放中', 'DISABLE':'已暂停', 'OFFLINE_BUDGET':'营销超出预算',
          'PRE_OFFLINE_BUDGET':'营销接近预算', 'OFFLINE_BALANCE':'账户余额不足',
          'ADVERTISER_OFFLINE_BUDGET':'账户预算不足', 'CAMPAIGN_OFFLINE_BUDGET':'计划组超出预算',
          'NO_SCHEDULE':'不在投放时段', 'TIME_DONE':'已完成', 'TIME_NO_REACH':'未到投放时间',
          'OFFLINE_AUDIT':'审核不通过', 'AUDIT':'审核中', 'FROZEN':'已终止'}
SCOPE = '当前配置账户的乘方商品计划（控成本及放量，含暂停/受限）；不含直播及全域计划。'


def term(command):
    text = re.sub(r'\s+', ' ', command.strip()).strip('。！？!?')
    if text in ('预算诊断', '诊断预算', '计划诊断'):
        return ''
    match = re.fullmatch(r'(?:预算诊断|计划诊断) (.+)', text)
    return match.group(1).strip() if match else None


def decimal(value, nonnegative=True):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and (not nonnegative or result >= 0) else None
    except (InvalidOperation, ValueError):
        return None


def _get(params, token):
    query = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (list, dict)) else v
                                   for k,v in params.items()})
    request = urllib.request.Request(LIST_URL + '?' + query, headers={'Access-Token':token})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def list_plans(env, day, request=None):
    account = env.get('QC_ADVERTISER_IDS', '').strip()
    if not account.isascii() or not account.isdigit():
        raise ValueError('预算诊断需要配置一个明确的千川账户。')
    result, pages, seen = [], [], set()
    for strategy in ('SMART_BID_CUSTOM', 'SMART_BID_CONSERVATIVE'):
        expected = None
        count_for_strategy = 0
        for page in range(1, 1001):
            params = {'advertiser_id':account, 'marketing_goal':'VIDEO_PROM_GOODS',
                      'adlab_scene':'OVERALL_PROJECT',
                      'filtering':{'status':'ALL',
                                                   'smart_bid_type':strategy},
                      'page':page, 'page_size':100}
            payload = qc_auth.call(env, lambda token: (request or _get)(params, token))
            try:
                if payload.get('code')!=0:raise ValueError()
                data = payload['data']
                info = data['page_info']
                batch = data['ad_list']
                total, total_pages = int(info['total_num']), int(info['total_page'])
                if not isinstance(batch, list) or int(info['page']) != page or total < 0 or total_pages < 0:
                    raise ValueError()
                if total == 0:
                    if page != 1 or batch or total_pages not in (0, 1):
                        raise ValueError()
                    pages.append({'strategy':strategy, 'response':payload})
                    break
                if total_pages < 1 or page > total_pages or not batch:
                    raise ValueError()
                if expected is None:
                    expected = (total, total_pages)
                if expected != (total, total_pages):
                    raise ValueError()
                for item in batch:
                    ad = item['ad_info']
                    pid = str(ad['id'])
                    if not pid.isascii() or not pid.isdigit() or pid in seen:
                        raise ValueError()
                    if ad.get('adlab_scene') not in (None, 'OVERALL_PROJECT'):
                        raise ValueError()
                    seen.add(pid)
                    result.append(item)
                count_for_strategy += len(batch)
                pages.append({'strategy':strategy, 'response':payload})
                if page == total_pages:
                    if count_for_strategy != total:
                        raise ValueError()
                    break
            except (KeyError, TypeError, ValueError):
                raise ValueError('计划列表字段异常、重复或分页不完整，未输出部分列表冒充完整结果。') from None
        else:
            raise ValueError('计划列表超过分页上限，诊断未完成。')
    return result, pages


def analyze(item, detail, day, read_at, cfg, performance=None, account=None):
    ad = item['ad_info']
    pid = str(ad['id'])
    measured,performance_status=budget_report.record(performance,pid,day,read_at,account)
    spend=measured['ad_spend'] if measured else None
    orders=measured['order_count'] if measured else None
    calculated=budget_report.ratios(measured)
    detail=detail if isinstance(detail,dict) else {'code':-1}
    data=detail.get('data') if isinstance(detail.get('data'),dict) else {}
    detail_ok = detail.get('code') == 0 and str(data.get('ad_id')) == pid and (
        account is None or data.get('advertiser_id') is None or str(data.get('advertiser_id'))==account) and (
        isinstance(data.get('delivery_setting'),dict))
    b = budgets.parse(detail if detail_ok else {'code':-1}, pid, read_at)
    setting = (data.get('delivery_setting') or {}) if detail_ok else {}
    status = data.get('status') if detail_ok else ad.get('status')
    opt_status = data.get('opt_status') if detail_ok else ad.get('opt_status')
    goal = decimal(setting.get('roi2_goal'))
    amount = decimal(b.get('amount'))
    near = Decimal(str(cfg.get('near_budget_ratio', '0.90')))
    minimum_spend = Decimal(str(cfg.get('min_spend', '200')))
    minimum_orders = Decimal(str(cfg.get('min_orders', '10')))
    ratio = spend / amount if b['status'] == 'ok' and b['mode'] == 'BUDGET_MODE_DAY' and amount and spend is not None else None
    missing = []
    if not detail_ok: missing.append('计划详情未获取')
    if b['status'] != 'ok': missing.append('预算未获取')
    if spend is None: missing.append('当日消耗缺失')
    if orders is None: missing.append('当日成交订单数缺失')
    if goal is None or goal <= 0: missing.append('后台ROI目标缺失或无效')
    if performance_status!='available':missing.append('当日经营报表未获取/无本计划记录/来源受限：'+performance_status)
    if detail_ok and data.get('adlab_scene') not in (None, 1, '1','OVERALL_PROJECT'):
        missing.append('计划已不属于乘方，需重新查询')
    scope = '经营值来自独立报表；G/A、N/A、N/C为不同复算值。平台目标与报表净ROI关系未确认，不计算达标率或推断利润。'
    if missing:
        judgment = '资料不全，暂不进入调预算判断'
    elif opt_status != 'ENABLE':
        judgment = '计划未明确开启，先核对操作状态'
    elif status in ('OFFLINE_BUDGET', 'PRE_OFFLINE_BUDGET'):
        judgment = '平台提示预算约束，需复核ROI口径及利润后再决定'
    elif status != 'DELIVERY_OK':
        judgment = '先处理投放状态限制，不因当前表现自动加预算'
    elif ratio is None:
        judgment = '预算类型不适用日预算占比，不判断日预算触顶'
    elif ratio < near:
        judgment = '当前消耗未接近日预算，暂无预算上限约束证据'
    elif spend < minimum_spend or orders < minimum_orders:
        judgment = '当前占比接近上限，但样本不足，继续观察'
    else:
        judgment = '预算约束观察候选，需复核ROI口径及利润，尚不能认定适合加预算'
    return {'plan_id':pid, 'plan_name':str(ad.get('name') or pid), 'performance_date':day,
            'fetched_at':read_at, 'status':status, 'opt_status':opt_status,
            'plan_type':'乘方商品', 'smart_bid_type':setting.get('smart_bid_type', ad.get('smart_bid_type')),
            'budget_snapshot':b, 'today_spend':str(spend) if spend is not None else None,
            'today_orders':str(orders) if orders is not None else None,
            'actual_comprehensive_roi':None, 'report_ratios':calculated,
            'report_amounts':{k:str(measured[k]) for k in budget_report.FIELDS} if measured else None,
            'performance_status':performance_status,
            'performance_cutoff':performance.get('cutoff') if isinstance(performance,dict) else None,
            'performance_fetched_at':performance.get('fetched_at') if isinstance(performance,dict) else None,
            'target_roi':str(goal) if goal is not None else None, 'roi_target_comparable':False,
            'deep_external_action':setting.get('deep_external_action'),
            'overall_roi_cost_items':setting.get('overall_roi_cost_items'),
            'alliance_commision_switch':setting.get('alliance_commision_switch'),
            'evidence_origin':'local_open_api_not_mcp',
            'roi_field_evidence':{'transport':'open_api','mcp_schema_verified':False,
                'target_field_path':'delivery_setting.roi2_goal','reported_metric_mapping_verified':False,
                'cost_item_enum_definition_verified':False,
                'settings':{key:{'present':key in setting,'value':setting.get(key)} for key in (
                    'roi2_goal','deep_external_action','overall_roi_cost_items','overall_cost_items',
                    'alliance_commision_switch','star_task_material_switch')}},
            'last_modified_at':data.get('modify_time') if detail_ok else None,
            'current_daily_budget_ratio':str(ratio) if ratio is not None else None,
            'missing':missing, 'judgment':judgment, 'roi_scope_note':scope,
            'execution_allowed':False}


def fetch(env, cfg=None, search='', list_request=None, detail_request=None, clock=None, report_request=None):
    cfg = cfg or {}
    for key in ('near_budget_ratio', 'min_spend', 'min_orders'):
        if key in cfg and decimal(cfg[key]) is None:
            raise ValueError('预算诊断阈值无效：'+key)
    if not 0 < Decimal(str(cfg.get('near_budget_ratio', '.90'))) <= 1:
        raise ValueError('预算接近比例必须在0至1之间')
    clock = clock or (lambda: datetime.now(CHINA))
    started = budget_report.instant(clock())
    day = started.date().isoformat()
    rows, raw = list_plans(env, day, list_request)
    selected = [item for item in rows if not search or search in str(item['ad_info'].get('name', '')) or search == str(item['ad_info']['id'])]
    account = env['QC_ADVERTISER_IDS'].strip()
    if selected:
        try:performance=budget_report.fetch(env,started,request=report_request,clock=clock)
        except Exception as error:performance=budget_report.unavailable(error)
    else:performance={'state':'not_requested','rows':{},'note':'无匹配计划，未额外请求报表'}
    def one(item):
        try:
            payload = qc_auth.call(env, lambda token: (detail_request or budgets._get)(account, str(item['ad_info']['id']), token))
        except Exception:
            payload = {'code':-1}
        return analyze(item, payload, day, budget_report.instant(clock()).isoformat(), cfg,
                       performance=performance,account=account)
    with ThreadPoolExecutor(max_workers=3) as pool:
        plans = list(pool.map(one, selected))
    return {'kind':'budget_diagnostics', 'advertiser_id':account,
            'requested_at':started.isoformat(), 'fetched_at':budget_report.instant(clock()).isoformat(),
            'performance_date':day, 'is_complete_day':False, 'scope':SCOPE,
            'metric_units':{'report_amounts':'CNY', 'today_spend':'CNY', 'budget':'CNY', 'roi':'dimensionless'},
            'query':search, 'total_plans_in_scope':len(rows), 'plans':plans, 'rules':cfg,
            'raw_list_pages':raw, 'raw_report_pages':performance.get('raw_pages',[]),
            'report_source':{k:v for k,v in performance.items() if k not in ('rows','raw_pages')},
            'evidence_origin':'local_open_api_not_mcp','transport':'open_api',
            'platform_roi_mapping':None,'roi_mapping_status':'UNCONFIRMED','execution_allowed':False}


def save(snapshot, root):
    folder = Path(root) / 'data' / 'qianchuan'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = re.sub('[^0-9]', '', snapshot['fetched_at'])
    path = folder / ('budget_diagnostics_%s_%s.json' % (snapshot['advertiser_id'], stamp))
    with path.open('x', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return path


def show(value, suffix=''):
    number = decimal(value)
    return format(number, ',.2f')+suffix if number is not None else '未获取'


def render(snapshot):
    plans = snapshot['plans']
    groups = [plans[i:i+4] for i in range(0, len(plans), 4)] or [[]]
    result = []
    read_time = datetime.fromisoformat(snapshot['fetched_at']).astimezone(CHINA).strftime('%Y-%m-%d %H:%M:%S')
    for page, group in enumerate(groups, 1):
        elements = [text_element('账户：%s\n%s 当日累计（尚未完整）；查询时间：%s（北京时间）\n%s\n匹配%d/%d条计划' %
                                (snapshot['advertiser_id'], snapshot['performance_date'], read_time, SCOPE, len(plans), snapshot['total_plans_in_scope']))]
        if not group:
            elements.append(text_element('未找到匹配计划；不将空列表当成零消耗。'))
        for p in group:
            b = p['budget_snapshot']
            budget_label = '未获取'
            if b['status'] == 'ok':
                budget_label = budgets.MODES[b['mode']] + (' '+show(b['amount'], ' 元') if b['amount'] is not None else '')
            ratio = p['current_daily_budget_ratio']
            ratio_label = show(Decimal(ratio)*100, '%') if ratio is not None else '不适用或未获取'
            elements += [{'tag':'hr'}, text_element('%s（%s）\n%s｜%s' % (p['plan_name'],p['plan_id'],p['plan_type'],STATES.get(p['status'],p['status'] or '状态未获取'))),
                         fields([('当前预算',budget_label),('当天广告消耗',show(p['today_spend'],' 元'))]),
                         fields([('当天成交订单数',show(p['today_orders'])),('消耗/当前日预算',ratio_label)]),
                         fields([('平台ROI目标（口径待核对）',show(p['target_roi'])),('整体支付ROI（G/A）',show(p['report_ratios']['payment_roi']))]),
                         fields([('净额/广告消耗（N/A复算）',show(p['report_ratios']['net_amount_div_ad_spend'])),
                                 ('净额/综合成本（N/C复算）',show(p['report_ratios']['net_amount_div_comprehensive_cost']))]),
                         text_element('经营报表截至：%s；报表读取：%s；预算详情读取：%s。'%(
                             p['performance_cutoff'] or '未获取',p['performance_fetched_at'] or '未获取',p['fetched_at'])),
                         text_element(p['judgment'] + ('\n缺少：'+'、'.join(p['missing']) if p['missing'] else ''))]
        rules = snapshot['rules']
        elements += [{'tag':'hr'}, text_element('观察参数：接近比例%s%%；样本参考%s元/%s单。属于可校准的诊断参数，不是平台规则。' %
                                                (Decimal(str(rules.get('near_budget_ratio','.9')))*100, rules.get('min_spend',200), rules.get('min_orders',10))),
                     text_element('Open API只读来源，未核验MCP schema。占比仅作读取时预算观察；不是实际触顶证明。复算值不是已确认的平台目标口径，不能推断达标或盈利；未修改预算。')]
        result.append(card('投流助手｜预算诊断 %d/%d' % (page,len(groups)), elements))
    return result


def reply(notifier, message_id, command, root, env, cfg, context, memory=None):
    chat = context.get('chat_id')
    if not chat or chat not in set(feishu_allowed_chat_ids(env) or [env.get('FEISHU_CHAT_ID')]):
        return None
    try:
        snapshot = fetch(env, cfg, search=term(command))
        save(snapshot, root)
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ServiceError, ValueError)) else '预算诊断查询或保存失败，请稍后重试。'
        return notifier.reply_card(message_id, card('投流助手｜预算诊断未完成', [text_element(message), text_element('未返回旧数据代替，未修改平台预算。')]))
    source = {k:v for k,v in snapshot.items() if k not in ('raw_list_pages','raw_report_pages')}
    answer = '\n'.join(p['plan_name']+'：'+p['judgment'] for p in snapshot['plans']) or '未找到匹配计划。'
    (memory or Conversation(Path(root)/'data/conversation.db', account=env.get('QC_ADVERTISER_IDS'))).save(chat,context.get('sender_id'),[{
        'question':command[:2000], 'answer':answer, 'source':json.dumps(source,ensure_ascii=False),
        'report_snapshot':None, 'display_kind':'answer'}])
    for built in render(snapshot):
        notifier.reply_card(message_id, built)
    return snapshot

# budget-report-source-v32
