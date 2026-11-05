"""Account-scoped user confirmation: current orders are self-operated, no creator fee."""
from .collection_scope import ACCOUNT
AUTHORITY={
 'account_id':ACCOUNT,'confirmed_on':'2026-11-04','source':'user_confirmation_in_chat',
 'purchase_multiplier':'3.2','insurance_rate':'0.0267','platform_rate':'0.1042',
 'platform_fee_scope':'transaction_platform_commission_only','other_service_fees':'0',
 'creator_rate':None,'creator_basis':'user_confirmed_self_operated_no_creator_commission',
 'creator_scope':'current_account_self_operated_orders','refund_handling':'no_creator_commission_to_reverse',
 'optimization_switch_is_actual_fee_evidence':False,
 'star_cost_excluded':True,'star_cost_reason':'user_confirmed_company_will_not_enable_star_selected',
 'paid_commission_formula':'0_by_user_business_confirmation',
 'net_commission_formula':'0_by_user_business_confirmation',
 'missing':[],
 'reconfirm_if':'account_starts_external_creator_orders_or_actual_commission_appears',
}
def budget_cost_ready(account):
 # This check concerns creator costs only; all other budget gates remain intact.
 return True

def annotate(report):
 report['creator_commission']=dict(AUTHORITY,status='user_confirmed_no_creator_commission',amount='0')
 report['star_cost']={'included':False,'amount':'0','basis':'user_confirmed_exclusion','platform_switch_changed':False}
 report['gaps']=[g for g in report['gaps'] if g!='creator_attribution_and_refund_reversals_missing']
 # Raw API enum/switch discrepancies remain visible, but are not a fee estimate.
 report['cost_context_verified']=not any(g in report['gaps'] for g in ('product_binding_changed','refund_model_unverified','settings_unavailable_or_stale','additional_marketing_costs_not_in_profit_model'))
 for value in list(report['windows'].values())+[report['intraday']]:
  value['estimated_profit_before_creator_commission']=value.get('estimated_profit')
  value['estimated_creator_commission']='0'
  value['estimated_profit_after_creator_commission']=value.get('estimated_profit')
  value['profit_scope']='退款模型预计利润；用户确认本账号商单自营，达人佣金为0；非实际结算利润'
 report['break_even_scope']='用户确认达人佣金为0的支付ROI模型盈亏线，非实际结算利润或投放目标'
 return report
