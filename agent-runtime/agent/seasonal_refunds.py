"""User-confirmed uniform seasonal scenarios; no SKU or platform-ROI inference."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import copy
from pathlib import Path
import shutil
import uuid

VERSION=30
# Sanitized fixture values only; production policy must be supplied and confirmed locally.
CONFIRMED_TABLE={
    'autumn_winter':{'overall':'0.41','le100':'0.42',
                    '101_200':'0.43','201_300':'0.44','gt300':'0.45'},
    'summer':{'overall':'0.46','le100':'0.47',
              '101_200':'0.48','201_300':'0.49','gt300':'0.50'}}


def decimal(value):
    try:
        result=Decimal(str(value))
        if not result.is_finite():raise ValueError()
        return result
    except (ValueError,TypeError,InvalidOperation):raise ValueError('季节退款数值无效') from None


def validate(policy,account):
    if (not isinstance(policy,dict) or policy.get('advertiser_id')!=account
            or policy.get('scene')!='goods' or policy.get('mode')!='seasonal_overall'
            or policy.get('status')!='CONFIRMED' or not policy.get('evidence')
            or policy.get('active_season') not in CONFIRMED_TABLE or policy.get('basis')!='full_amount_refund_scenario'
            or not isinstance(policy.get('rates'),dict) or set(policy['rates'])!=set(CONFIRMED_TABLE)):
        raise ValueError('季节整体退款策略的账户、范围、季节或确认依据无效')
    for value in policy['rates'].values():
        if not 0<=decimal(value)<=1:raise ValueError('退款率须为0至1的小数')


def rate_for(policy,match):
    if (not match or match.get('advertiser_id')!=policy['advertiser_id'] or match.get('scene')!='goods'
            or match.get('season') not in policy['rates']):return None
    try:
        if date.fromisoformat(match['start'])>date.fromisoformat(match['end']):return None
    except (KeyError,ValueError,TypeError):return None
    return decimal(policy['rates'][match['season']])


def references(rules):
    from . import context_finance
    policy=rules.get('seasonal_refund_policy')
    if policy is None:return {}
    margin=context_finance.validate_rules(rules,rules['advertiser_id'])
    return {season:{'refund_rate':str(decimal(rate)),'contribution_margin':str(margin),
                    'breakeven_payment_roi':str(1/((1-decimal(rate))*margin)) if decimal(rate)<1 else None,
                    'basis':'季节整体全额退款情景；支付ROI=成交/广告消耗，不是平台净成交ROI目标',
                    'active':season==policy['active_season']}
            for season,rate in policy['rates'].items()}


def configure(root,account,season,*,apply=False,now=None):
    from . import trading_context as context,context_sources as sources,context_finance as finance
    from .reliability import file_lock
    root=Path(root).resolve();now=now or datetime.now(context.CHINA)
    if now.tzinfo is None:raise ValueError('配置时间必须带时区')
    now=now.astimezone(context.CHINA)
    if season not in CONFIRMED_TABLE:raise ValueError('须显式选择summer或autumn_winter')
    home=context.account_home(root,account)
    original_path=sources.inside(root,root/'config/rules.json')
    cfg,source_digest=sources.load_json(original_path)
    source_rates=cfg.get('model',{}).get('refund_rates',{})
    for key,table in CONFIRMED_TABLE.items():
        for band,expected in table.items():
            if decimal(source_rates.get(key,{}).get(band))!=decimal(expected):
                raise ValueError('原始季节退款表与此前已确认数据不一致；未自动启用')
    policy={'schema_version':1,'policy_version':'seasonal30_user-confirmed-2026-10-29',
            'advertiser_id':account,'scene':'goods','mode':'seasonal_overall',
            'status':'CONFIRMED','active_season':season,'basis':'full_amount_refund_scenario',
            'rates':{s:values['overall'] for s,values in CONFIRMED_TABLE.items()},
            'price_band_reference':copy.deepcopy(CONFIRMED_TABLE),'price_band_status':'REFERENCE_NOT_ACTIVE',
            'evidence':'用户2026-10-29确认：商品统一按照之前夏季和冬季分别算退货率，数据沿用此前给出。按商品统一使用季节整体率。',
            'source':'config/rules.json:model.refund_rates','source_config_sha256':source_digest,
            'limitation':'历史退款情景，不是实时退款或结算利润；不按商品名/日期猜季节，不套旧计划ID分档。未标季节的历史缓存使用active_season并标注fallback。'}
    validate(policy,account)
    path=sources.inside(root,home/'01_rules/rules.json')
    if not apply:
        rules=sources.load_json(path)[0] if path.exists() else finance.defaults(account)
        finance.validate_rules(rules,account)
        updated=copy.deepcopy(rules);updated['seasonal_refund_policy']=policy
        finance.validate_rules(updated,account)
        return {'applied':False,'changed':rules.get('seasonal_refund_policy')!=policy,
                'policy':policy,'references':references(updated),'backup':None}
    context.initialize(root,account)
    with file_lock(path.with_suffix('.seasonal.lock')):
        rules,_=sources.load_json(path);finance.validate_rules(rules,account)
        changed=rules.get('seasonal_refund_policy')!=policy
        updated=copy.deepcopy(rules);updated['seasonal_refund_policy']=policy
        finance.validate_rules(updated,account)
        result={'applied':bool(apply),'changed':changed,'policy':policy,'references':references(updated),'backup':None}
        if not apply or not changed:return result
        # Re-check source table evidence after obtaining the rules lock.
        if sources.load_json(original_path)[1]!=source_digest:raise ValueError('原始规则在预览期间变更，请重新核对')
        backup=sources.inside(root,root/'_update_backups'/('seasonal_v30_'+now.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:10])/'rules.json')
        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup)
        updated['rule_version']=str(rules['rule_version'])+'+seasonal30_'+now.strftime('%Y%m%d_%H%M%S')
        context.write_json(path,updated);result['backup']=backup.relative_to(root).as_posix()
        return result
