import unittest,tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from qc_shadow.api import create_app
import test_data
class ApiTests(unittest.TestCase):
 setUp=test_data.DataTests.setUp
 def test_auth_read_only_and_missing_token(self):
  app=create_app(self.root,'a'*32);client=TestClient(app)
  self.assertEqual(client.get('/v1/accounts').status_code,401)
  headers={'Authorization':'Bearer '+'a'*32}
  self.assertEqual(client.get('/v1/accounts',headers=headers).json(),{'accounts':['9']})
  self.assertEqual(client.post('/v1/accounts',headers=headers).status_code,405)
  self.assertEqual(client.get('/v1/accounts/9/evidence',headers=headers).status_code,200)
  self.assertFalse((self.root/'shadow.db').exists())
  with self.assertRaises(ValueError):create_app(self.root,'')
 def test_no_write_routes(self):
  app=create_app(self.root,'b'*32)
  self.assertTrue(all(route.methods=={'GET'} for route in app.routes))
