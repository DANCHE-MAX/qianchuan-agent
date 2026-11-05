"""User-confirmed model mappings, kept separate from historical price evidence."""
import hashlib,json

def model_hash(model):
 # Binding intentionally excludes unrelated notification/runtime configuration.
 selected={k:model.get(k) for k in ('season','version','refund_rates')}
 return hashlib.sha256(json.dumps(selected,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def apply_authority(snapshot,authority,model):
 if (authority.get('account_id')!=snapshot['account_id'] or authority.get('historical_use') is not True
     or authority.get('basis')!='user_confirmed_model_assumption' or not authority.get('evidence')
     or authority.get('model_sha256')!=model_hash(model)):return snapshot
 for p in snapshot['plans']:
  band=authority.get('plans',{}).get(p['plan_id']);rate=model.get('refund_rates',{}).get(model.get('season'),{}).get(band)
  if rate is None:continue
  p['refund']={'band':band,'rate':str(rate),'model_version':model['version'],'season':model['season'],
   'verified':True,'verification_evidence':authority['evidence'],'basis':authority['basis'],
   'expected_product_ids':authority.get('product_ids',{}).get(p['plan_id'],[]),
   'historical_use':True,'historical_price_verified':False,'authority_sha256':hashlib.sha256(json.dumps(authority,sort_keys=True).encode()).hexdigest()}
 return snapshot
