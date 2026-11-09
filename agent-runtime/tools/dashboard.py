#!/usr/bin/env python3
"""Start local read-only dashboard; no scheduler or notifier imports."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.dashboard_server import make_server
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--shadow-root',type=Path);p.add_argument('--port',type=int,default=8768);a=p.parse_args()
 authority=json.loads((a.root/'data/model_policy/authority.json').read_text())
 server=make_server(a.root,authority['account'],port=a.port,shadow_root=a.shadow_root)
 print('Read-only dashboard: http://127.0.0.1:'+str(a.port),flush=True)
 try:server.serve_forever()
 except KeyboardInterrupt:server.server_close()
