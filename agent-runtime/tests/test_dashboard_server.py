import unittest,tempfile,threading,urllib.request,urllib.error
from pathlib import Path
from agent.dashboard_server import make_server
class ServerTests(unittest.TestCase):
 def test_no_write_or_arbitrary_file_routes(self):
  with tempfile.TemporaryDirectory() as t:
   server=make_server(Path(t),'demo',port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    base='http://127.0.0.1:'+str(server.server_port)
    self.assertEqual(urllib.request.urlopen(base+'/').status,200)
    for req in [base+'/../../config/.env',urllib.request.Request(base+'/api/data',method='POST'),base+'/api/data?date=2099-01-01']:
     with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(req)
     self.assertIn(cm.exception.code,(400,404,405))
    with self.assertRaises(ValueError):make_server(Path(t),'demo',host='0.0.0.0',port=0)
   finally:server.shutdown();server.server_close();thread.join()
