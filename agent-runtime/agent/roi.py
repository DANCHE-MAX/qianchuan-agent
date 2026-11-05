"""Primary reporting ROI: total paid GMV divided by overall ad spend."""
from decimal import Decimal


def payment_roi(item):
    spend = item.get('ad_spend')
    gmv = item.get('gmv')
    if spend is None or gmv is None or not Decimal(str(spend)):
        return None
    return Decimal(str(gmv)) / Decimal(str(spend))


def normalize_report(report):
    for item in [report.get('totals', {}), *report.get('plans', [])]:
        value = payment_roi(item)
        item['payment_roi'] = str(value) if value is not None else None
    report['roi_basis'] = '主ROI为整体支付ROI：整体成交金额/整体消耗。comprehensive_roi为旧综合营销辅助指标，禁止替代主ROI；零消耗时主ROI未定义。'
    return report
