"""Loopback-only read-only HTTP application. Explicit routes, no filesystem serving."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from datetime import datetime,timedelta,date
from zoneinfo import ZoneInfo
import json
from . import decision_dashboard,profit_policy


def make_server(root,account,*,host='127.0.0.1',port=8768,shadow_root=None):
 if host!='127.0.0.1':raise ValueError('Loopback only')
 root=Path(root)
 assets=Path(__file__).resolve().parents[1]/'dashboard'
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def send_body(self,code,body,kind='application/json; charset=utf-8'):
   self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'");self.send_header('Content-Length',str(len(body)));self.end_headers()
   if self.command!='HEAD':self.wfile.write(body)
  def do_POST(self):self.send_body(405,b'{"error":"read_only"}')
  do_PUT=do_POST;do_DELETE=do_POST;do_PATCH=do_POST
  def do_HEAD(self):self.do_GET()
  def do_GET(self):
   # Stop DNS-rebinding and cross-origin scripted reads, including localhost aliases.
   if self.headers.get('Host','') not in ('127.0.0.1:'+str(self.server.server_port),'127.0.0.1'):
    self.send_body(403,b'{"error":"host"}');return
   if self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port)):
    self.send_body(403,b'{"error":"origin"}');return
   req=urlsplit(self.path);params=parse_qs(req.query)
   if req.path in ('/','/app.js','/style.css'):
    file={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[req.path]
    self.send_body(200,(assets/file).read_bytes(),{'/':'text/html; charset=utf-8','/app.js':'application/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}[req.path]);return
   if req.path not in ('/api/data','/api/scenario','/api/artifact'):
    self.send_body(404,b'{"error":"route"}');return
   try:
    yesterday=datetime.now(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=1);day=params.get('date',[str(yesterday)])[0];selected=date.fromisoformat(day)
    if selected>yesterday or selected<date(2020,1,1):raise ValueError('Complete dates only')
    if req.path=='/api/artifact':
     files=decision_dashboard.attachments(root,str(account),day);index=int(params.get('index',['-1'])[0])
     if index<0 or index>=len(files):raise ValueError('Unknown artifact')
     f=files[index];self.send_body(200,f['path'].read_bytes(),'image/png' if f['kind']=='image' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');return
    data=decision_dashboard.build(root,str(account),day,shadow_root)
    try:data['attachments']=[{'index':i,'name':f['name'],'kind':f['kind']} for i,f in enumerate(decision_dashboard.attachments(root,str(account),day))]
    except (ValueError,OSError,KeyError):data['attachments']=[];data['warnings'].append('附件证据待核验')
    if req.path=='/api/scenario':
     if not data['policy']:raise ValueError('Policy pending')
     policy=profit_policy.resolve_policy(data['policy'],params.get('plan',[''])[0])
     if 'refund' in params:policy=dict(policy,refund_rate=params['refund'][0],refund_source='local_scenario_only')
     data=dict(profit_policy.scenario(params.get('spend',['0'])[0],policy),policy=policy)
    self.send_body(200,json.dumps(data,ensure_ascii=False).encode())
   except (ValueError,KeyError,ArithmeticError):self.send_body(400,b'{"error":"invalid_or_pending"}')
   except Exception:self.send_body(503,b'{"error":"local_data_pending"}')
 return ThreadingHTTPServer((host,port),Handler)
