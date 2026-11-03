import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from upload_pixeldrain import upload_file


class PixeldrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / 'ROM test.zip'
        self.file.write_bytes(b'firmware fixture')
        self.session = Mock()

    def response(self, status=201, **updates):
        response = Mock()
        response.status_code = status
        response.json.return_value = dict(success=True, id='abc123', size=self.file.stat().st_size) | updates
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_streaming_upload_and_auth(self):
        def put(url, **kwargs):
            self.assertEqual(url, 'https://pixeldrain.com/api/file/ROM%20test.zip')
            self.assertEqual(kwargs['auth'], ('', 'test-secret'))
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['data'].read(), self.file.read_bytes())
            return self.response()
        self.session.put.side_effect = put
        self.assertEqual(upload_file(self.file, 'test-secret', self.session), 'https://pixeldrain.com/u/abc123')

    def test_missing_secret_fails_without_request(self):
        with self.assertRaises(ValueError):
            upload_file(self.file, '', self.session)
        self.session.put.assert_not_called()

    def test_file_info_without_success_flag(self):
        reply = self.response()
        del reply.json.return_value['success']
        self.session.put.return_value = reply
        self.assertEqual(upload_file(self.file, 'test-secret', self.session), 'https://pixeldrain.com/u/abc123')
        self.session.get.assert_not_called()

    def test_id_only_response_uses_info_without_reupload(self):
        reply = self.response()
        reply.json.return_value = {'id': 'abc123'}
        self.session.put.return_value = reply
        self.session.get.return_value = self.response(200)
        self.assertEqual(upload_file(self.file, 'test-secret', self.session), 'https://pixeldrain.com/u/abc123')
        self.session.put.assert_called_once()
        self.session.get.assert_called_once_with('https://pixeldrain.com/api/file/abc123/info', timeout=(20, 30), allow_redirects=False)

    def test_info_failure_does_not_reupload(self):
        reply = self.response()
        reply.json.return_value = {'id': 'abc123'}
        self.session.put.return_value = reply
        self.session.get.side_effect = requests.ConnectionError('info unavailable')
        with self.assertRaisesRegex(RuntimeError, 'already uploaded to https://pixeldrain.com/u/abc123'):
            upload_file(self.file, 'test-secret', self.session)
        self.session.put.assert_called_once()

    def test_api_failures_and_size_mismatch(self):
        for reply in (self.response(401), self.response(413), self.response(success=False),
                      self.response(id='../bad'), self.response(size=999), self.response(302)):
            self.session.put.return_value = reply
            with self.assertRaises((ValueError, RuntimeError)):
                upload_file(self.file, 'test-secret', self.session)

    def test_retry_rewinds_body(self):
        calls = []
        def put(url, **kwargs):
            calls.append(kwargs['data'].read())
            if len(calls) == 1:
                raise requests.ConnectionError('interrupted upload')
            return self.response()
        self.session.put.side_effect = put
        with patch('upload_pixeldrain.time.sleep'):
            upload_file(self.file, 'test-secret', self.session)
        self.assertEqual(calls, [self.file.read_bytes()] * 2)

    def test_bad_json_fails(self):
        reply = self.response()
        reply.json.side_effect = ValueError('invalid JSON')
        self.session.put.return_value = reply
        with self.assertRaises(ValueError):
            upload_file(self.file, 'test-secret', self.session)
