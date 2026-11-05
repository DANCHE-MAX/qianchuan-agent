import unittest
from unittest.mock import patch
from qc_shadow.read_transport import ReadClient,ReadFailure
class TransportTests(unittest.TestCase):
 def test_write_endpoint_and_other_account_rejected_before_io(self):
  c=ReadClient(account='9',token_loader=lambda:'test')
  with patch('urllib.request.OpenerDirector.open') as network:
   with self.assertRaises(ValueError):c.get('/open_api/v1.0/qianchuan/uni_promotion/ad/status/update/',{'advertiser_id':'9'})
   with self.assertRaises(ValueError):c.get(c.LIST,{'advertiser_id':'8'})
   network.assert_not_called()
 def test_error_does_not_expose_payload(self):
  c=ReadClient(account='9',token_loader=lambda:'test')
  from io import BytesIO
  with patch('urllib.request.OpenerDirector.open',return_value=BytesIO(b'{"code":40002,"message":"private"}')):
   with self.assertRaises(ReadFailure) as e:c.get(c.LIST,{'advertiser_id':'9'})
  self.assertNotIn('private',str(e.exception));self.assertEqual(e.exception.code,40002)
