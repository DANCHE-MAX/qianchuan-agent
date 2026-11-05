"""Explicit read-only acquisition, separate from GET evidence API. No refresh/write routes."""
import json,urllib.request,urllib.parse,urllib.error
from .collection_scope import ACCOUNT,START
from pathlib import Path
class ReadFailure(Exception):
    def __init__(self,code=None,http=None,request_id=None,category=None):
        self.code,self.http,self.request_id=code,http,request_id
        self.category=category
        super().__init__('readonly_api_failed code=%s http=%s'%(code,http))
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ReadFailure(http=302)
class ReadClient:
    BASE='https://api.oceanengine.com'
    LIST='/open_api/v1.0/qianchuan/uni_promotion/list/'
    DETAIL='/open_api/v1.0/qianchuan/uni_promotion/ad/detail/'
    REPORT='/open_api/v1.0/qianchuan/report/uni_promotion/data/get/'
    CONFIG='/open_api/v1.0/qianchuan/report/uni_promotion/config/get/'
    LOGS='/open_api/v1.0/qianchuan/tools/log_search/'
    PRODUCTS='/open_api/v1.0/qianchuan/product/available/get/'
    ALLOWED={LIST,DETAIL,REPORT,CONFIG,LOGS,PRODUCTS}
    def __init__(self,account,token_loader):self.account=str(account);self.token_loader=token_loader;self.receipts=[]
    @classmethod
    def from_runtime(cls,root):
        path=Path(root)/'config/.env'
        def read():
            env={}
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                if line.strip().startswith('#') or '=' not in line:continue
                k,v=line.split('=',1)
                if k.strip() in ('QC_ADVERTISER_IDS','QC_ACCESS_TOKEN'):env[k.strip()]=v.strip()
            return env
        account=read().get('QC_ADVERTISER_IDS','')
        if not account.isascii() or not account.isdigit():raise ValueError('single_account_required')
        def token():
            env=read()
            if env.get('QC_ADVERTISER_IDS')!=account:raise ValueError('account_changed')
            return env.get('QC_ACCESS_TOKEN','')
        return cls(account,token)
    def get(self,path,params):
        if path not in self.ALLOWED or str(params.get('advertiser_id'))!=self.account:raise ValueError('readonly_scope_violation')
        if self.account==ACCOUNT and path in (self.REPORT,self.LOGS):
            start=str(params.get("start_time", ""))[:10]
            if not start or start<str(START):raise ValueError("collection_start_violation")
        token=self.token_loader()
        if not token:raise ReadFailure(code='missing_authorization')
        query=urllib.parse.urlencode({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in params.items()})
        req=urllib.request.Request(self.BASE+path+'?'+query,headers={'Access-Token':token},method='GET')
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=30) as response:payload=json.load(response)
        except urllib.error.HTTPError as e:raise ReadFailure(http=e.code) from None
        except (OSError,ValueError):raise ReadFailure(code='transport_or_json') from None
        receipt={'endpoint':path,'code':payload.get('code'),'request_id':payload.get('request_id') or payload.get('log_id')}
        message=str(payload.get('message',''))
        receipt['category']='rate_limited' if ('频率超限' in message or '频控' in message) else None
        self.receipts.append(receipt)
        if payload.get('code')!=0:raise ReadFailure(code=payload.get('code'),request_id=receipt['request_id'],category=receipt['category'])
        return payload
