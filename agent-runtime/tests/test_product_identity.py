import json,hashlib
from copy import deepcopy
from agent import product_identity as p

def setup(tmp_path):
 f=tmp_path/'name.json';f.write_text('{}')
 d={'account':'a','plan_id':'p','product_id':'g','name':'示例商品','sku':None,'name_evidence':str(f),'name_sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'bindings':{}}
 folder=tmp_path/'data/product_identity';folder.mkdir(parents=True);(folder/'confirmed.json').write_text(json.dumps([d]));return d,folder

def test_overlay_no_mutation_and_missing_sku(tmp_path):
 d,folder=setup(tmp_path);f=tmp_path/'detail.json';f.write_text(json.dumps({'code':0,'data':{'product_infos':[{'product_id':'g'}]}}));d['bindings']['2026-11-07']={'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()};(folder/'confirmed.json').write_text(json.dumps([d]))
 r={'account':'a','date':'2026-11-07','rows':[{'plan_id':'p','name':'test','profit':'-1'}]};before=deepcopy(r)
 out,m=p.overlay(tmp_path,r,None)
 assert r==before and out['rows'][0]['profit']=='-1'
 assert '示例商品' in out['rows'][0]['name'] and '款号待核验' in out['rows'][0]['name']
 assert m['rows'][0]['product']=='g'
 r['date']='2026-11-08';assert p.overlay(tmp_path,r,None)[0]==r
 f.write_text('{}');r['date']='2026-11-07';assert p.overlay(tmp_path,r,None)[0]==r

def test_conflict_and_multiple_products(tmp_path):
 d,_=setup(tmp_path)
 assert p.match(d,{'code':0,'data':{'product_infos':[{'product_id':'other'}]}}) is False
 assert p.match(d,{'code':0,'data':{'product_infos':[{'product_id':'g'},{'product_id':'other'}]}}) is False
 assert p.match(d,{'code':1,'data':{'product_infos':[{'product_id':'g'}]}}) is False
 assert p.records(tmp_path,'other')==[]

def test_current_capture(tmp_path):
 d,_=setup(tmp_path);calls=[]
 def get(env,path,args):
  calls.append(args);return {'code':0,'data':{'product_infos':[{'product_id':'g'}]}}
 r={'account':'a','date':'2026-11-08','rows':[{'plan_id':'p','name':'test','profit':None}]}
 # Fake numeric API identity for current-fetch tests.
 d['plan_id']='12';(tmp_path/'data/product_identity/confirmed.json').write_text(json.dumps([d]));r['rows'][0]['plan_id']='12'
 out=p.capture(tmp_path,{},r,tmp_path/'source',request=get)
 assert len(calls)==1 and out['rows'][0]['product_id']=='g' and out['rows'][0]['profit'] is None
 assert (tmp_path/'source/product-identities.json').exists()

def test_mapping_conflict_preserved(tmp_path):
 d,folder=setup(tmp_path);f=tmp_path/'detail.json';f.write_text(json.dumps({'code':0,'data':{'product_infos':[{'product_id':'g'}]}}));d['bindings']['2026-11-07']={'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()};(folder/'confirmed.json').write_text(json.dumps([d]))
 r={'account':'a','date':'2026-11-07','rows':[{'plan_id':'p','name':'test'}]};m={'day':r['date'],'account':'a','rows':[{'plan':'p','product':'different','name':'other'}]}
 assert p.overlay(tmp_path,r,m)==(r,m)
 day={'rows':[{'plan':'p','product':None,'name':'','amount':'2'}]};named=p.overlay(tmp_path,r,None)[0];p.merge_rows(day,named);assert day['rows'][0]['product']=='g' and day['rows'][0]['amount']=='2'

import unittest,tempfile
class ProductIdentityTests(unittest.TestCase):
    pass
for _name,_fn in list(globals().items()):
    if _name.startswith('test_'):
        def _test(self,fn=_fn):
            with tempfile.TemporaryDirectory() as directory:fn(__import__('pathlib').Path(directory))
        setattr(ProductIdentityTests,_name,_test)
