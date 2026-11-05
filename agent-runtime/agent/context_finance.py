"""Versioned, explicit business assumptions; no platform target-ROI conversion."""
from datetime import date
from decimal import Decimal, InvalidOperation


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('经营数值须为有限非负数') from None


def defaults(account):
    return {'schema_version': 1, 'rule_version': 'user-confirmed-2026-10-29',
            'advertiser_id': account, 'status': 'CONFIRMED',
            'evidence': '用户已确认：售价为采购成本3.2倍，保险2.67%，平台10.42%，移除达人佣金。',
            'purchase_multiplier': '3.2', 'insurance_rate': '0.0267', 'platform_rate': '0.1042',
            'commission_rate': '0', 'refund_assumptions': [], 'product_bindings': [],
            'roi_basis': '整体成交金额/整体广告消耗；零消耗未定义',
            'cost_basis': '退款后预计净成交作为采购、运费险及平台费估算基数；广告消耗独立扣减',
            'limitation': '情景利润不是结算利润；缺少适用退款率时不输出利润和保本ROI。'}


def validate_rules(rules, account):
    if (rules.get('schema_version') != 1 or rules.get('advertiser_id') != account
            or rules.get('status') != 'CONFIRMED' or not rules.get('evidence') or not rules.get('rule_version')):
        raise ValueError('经营规则的账户、版本或确认依据无效')
    multiplier = number(rules['purchase_multiplier'])
    if not multiplier:
        raise ValueError('售价倍率必须大于零')
    fees = [number(rules[k]) for k in ('insurance_rate', 'platform_rate', 'commission_rate')]
    if any(x > 1 for x in fees) or fees[2] != 0:
        raise ValueError('成本费率无效，本版不计达人佣金')
    margin = Decimal(1) - Decimal(1)/multiplier - fees[0] - fees[1]
    if margin <= 0:
        raise ValueError('广告前贡献比例必须大于零')
    if not isinstance(rules.get('refund_assumptions'), list) or not isinstance(rules.get('product_bindings'), list):
        raise ValueError('退款假设和商品绑定必须为列表')
    if rules.get('seasonal_refund_policy') is not None:
        from . import seasonal_refunds
        seasonal_refunds.validate(rules['seasonal_refund_policy'], account)
    return margin


def refund_for(rules, match):
    if not match or not all(match.get(k) for k in ('advertiser_id', 'sku', 'scene', 'start', 'end')):
        return None
    matches = []
    for item in rules['refund_assumptions']:
        if (item.get('status') != 'CONFIRMED' or not item.get('evidence')
                or item.get('basis') != 'full_amount_refund_scenario'
                or any(item.get(k) != match[k] for k in ('advertiser_id', 'sku', 'scene'))):
            continue
        try:
            start, end = date.fromisoformat(match['start']), date.fromisoformat(match['end'])
            if not date.fromisoformat(item['valid_from']) <= start <= end <= date.fromisoformat(item['valid_to']):
                continue
            rate = number(item['rate'])
            if rate <= 1:
                matches.append(rate)
        except (KeyError, ValueError):
            continue
    # Even identical overlapping entries require reconciliation; do not silently pick one.
    return matches[0] if len(matches) == 1 else None


def calculate(gmv, spend, rules, match):
    margin = validate_rules(rules, rules['advertiser_id'])
    gmv, spend = number(gmv), number(spend)
    if rules.get('seasonal_refund_policy') is not None:
        from . import seasonal_refunds
        rate = seasonal_refunds.rate_for(rules['seasonal_refund_policy'], match)
    else:
        rate = refund_for(rules, match)
    result = {'payment_roi': str(gmv/spend) if spend else None,
              'contribution_margin': str(margin), 'commission_cost': '0',
              'refund_rate': str(rate) if rate is not None else None,
              'predicted_net': None, 'purchase_cost': None, 'insurance_cost': None,
              'platform_cost': None, 'estimated_total_cost': None, 'predicted_profit': None,
              'breakeven_payment_roi': None, 'platform_target_roi': None,
              'profit_basis': '历史退款情景推算，不代表实际结算；非平台净成交ROI目标。',
              'refund_season': match.get('season') if match and rules.get('seasonal_refund_policy') else None,
              'refund_policy_mode': 'seasonal_overall' if rules.get('seasonal_refund_policy') else 'scoped_refund'}
    if rate is not None:
        net = gmv * (1-rate)
        purchase = net/number(rules['purchase_multiplier'])
        insurance = net*number(rules['insurance_rate'])
        platform = net*number(rules['platform_rate'])
        costs = purchase + insurance + platform + spend
        result.update(predicted_net=str(net), purchase_cost=str(purchase), insurance_cost=str(insurance),
                      platform_cost=str(platform), estimated_total_cost=str(costs),
                      predicted_profit=str(net-costs),
                      breakeven_payment_roi=str(1/((1-rate)*margin)) if rate < 1 else None)
    return result

# seasonal-context-v30
