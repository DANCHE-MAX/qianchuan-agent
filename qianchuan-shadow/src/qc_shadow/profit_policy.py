"""Pure, versioned read-only profit policy. Identical source ships in both packages."""
from decimal import Decimal,ROUND_HALF_UP,ROUND_CEILING
import json
from pathlib import Path
VERSION='unified-readonly-v1'

def number(value):
 n=Decimal(str(value))
 if not n.is_finite() or n<0:raise ValueError('Expected finite nonnegative number')
 return n

def money(value):return value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)

def resolve_policy(policy,plan_id):
 p={k:v for k,v in policy.items() if k!='plan_refunds'}
 if policy.get('model_version')==VERSION:
  entry=policy.get('plan_refunds',{}).get(str(plan_id))
  p.update(refund_rate=entry['rate'] if entry else policy['refund_rate'],refund_band=entry['band'] if entry else 'overall',refund_fallback=not bool(entry),refund_source=entry.get('source','user_confirmed') if entry else 'confirmed_overall_fallback')
 if number(p['refund_rate'])>1:raise ValueError('Invalid refund rate')
 return p

def estimate(gmv,spend,p):
 rate=number(p['refund_rate']);multiplier=number(p['purchase_multiplier']);fee=number(p['fee_rate']);ad=number(p['ad_rate'])
 if rate>1 or multiplier==0 or fee>1 or ad>1 or p.get('shipping_mode')!='excluded':raise ValueError('Invalid profit policy')
 keys=('net','cost','fee','preprofit','profit','ad15','profit15','headroom','candidate')
 if gmv is None or spend is None:return dict.fromkeys(keys)
 gmv,spend=number(gmv),number(spend);net=money(gmv*(1-rate));cost=money(net/multiplier);fees=money(net*fee);pre=net-cost-fees;profit=pre-spend;ad15=money(gmv*ad)
 return {k:str(money(v)) for k,v in dict(net=net,cost=cost,fee=fees,preprofit=pre,profit=profit,ad15=ad15,profit15=pre-ad15,headroom=max(Decimal(0),profit),candidate=max(Decimal(0),min(pre,ad15)-spend)).items()}

def scenario(extra_spend,p):
 extra=number(extra_spend);rate=number(p['refund_rate']);m=number(p['purchase_multiplier']);fee=number(p['fee_rate'])
 if m==0 or rate>1 or fee>1:raise ValueError('Invalid scenario policy')
 coefficient=(1-rate)*(1-1/m-fee)
 if coefficient<=0:raise ValueError('No positive contribution')
 return {'extra_spend':str(extra),'required_gmv':str((extra/coefficient).quantize(Decimal('.01'),rounding=ROUND_CEILING)),'coefficient':str(coefficient),'note':'覆盖新增广告费的理论GMV，不弥补既有亏损，不预测投放收入，不代表预算建议；未计运费。'}

def load_policy(root,account,base):
 path=Path(root)/'data/model_policy/authority.json'
 if not path.exists():raise ValueError('Unified policy authority missing')
 data=json.loads(path.read_text())
 if str(data['account'])!=str(account) or data['status']!='CONFIRMED':raise ValueError('Policy authority scope mismatch')
 return dict(base,refund_rate=data['overall_rate'],model_version=VERSION,purchase_multiplier='3.2',fee_rate='.1310',shipping_mode='excluded',plan_refunds=data['plan_refunds'],authority_version=data['version'])

def shadow_policy(refund):
 return dict(model_version=VERSION,refund_rate=refund['rate'],refund_band=refund['band'],refund_fallback=refund['band']=='overall',refund_source=refund.get('verification_evidence','user_confirmed'),purchase_multiplier='3.2',fee_rate='.1310',ad_rate='.15',shipping_mode='excluded')
