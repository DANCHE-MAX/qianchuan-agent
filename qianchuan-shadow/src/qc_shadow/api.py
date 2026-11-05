"""Read-only HTTP projection. No collectors, mutations, schedulers or execution routes."""
from datetime import datetime
import secrets,sqlite3
from zoneinfo import ZoneInfo
from fastapi import FastAPI,Depends,HTTPException
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from .adapter import Source

def create_app(source_root,token,history_path=None):
    if not token or len(token)<32:raise ValueError('dedicated_shadow_token_required_min_32_characters')
    security=HTTPBearer(auto_error=False)
    def authenticate(auth:HTTPAuthorizationCredentials|None=Depends(security)):
        if auth is None or not secrets.compare_digest(auth.credentials,token):raise HTTPException(401,'unauthorized')
    app=FastAPI(title='Qianchuan read-only shadow',docs_url=None,redoc_url=None,openapi_url=None,dependencies=[Depends(authenticate)])
    source=Source(source_root,history_path=history_path)
    @app.get('/v1/health')
    def health():return {'mode':'read_only','execution_enabled':False,'rule_clock_started':False}
    @app.get('/v1/accounts')
    def accounts():
        try:return {'accounts':source.accounts()}
        except (OSError,sqlite3.DatabaseError,ValueError):raise HTTPException(503,'source_unavailable') from None
    @app.get('/v1/accounts/{account}/evidence')
    def evidence(account:str):
        if not account.isascii() or not account.isdigit():raise HTTPException(400,'invalid_account')
        try:return source.snapshot(account,datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
        except (OSError,sqlite3.DatabaseError,ValueError,KeyError,TypeError):raise HTTPException(503,'source_unavailable_or_invalid') from None
    return app
