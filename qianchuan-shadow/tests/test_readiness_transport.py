import unittest
from qc_shadow.read_transport import ReadClient
class ProductScopeTests(unittest.TestCase):
 def test_product_get_allowed(self):
  self.assertIn('/open_api/v1.0/qianchuan/product/available/get/',ReadClient.ALLOWED)
 def test_no_write_paths(self):
  self.assertTrue(all(not any(w in p for w in ('update','create','delete','status/')) for p in ReadClient.ALLOWED))
