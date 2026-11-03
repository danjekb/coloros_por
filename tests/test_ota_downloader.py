import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from oplus_ota_downloader import OtaDownloader, ProbeResult, SecureSession
import requests


def response(status=200, data=b'abc', headers=None):
    r = requests.Response()
    r.status_code = status
    r.url = 'https://example.com/rom.zip'
    r.headers.update(headers or {})
    r.raw = io.BytesIO(data)
    return r


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dl = OtaDownloader(output_dir=self.tmp.name, retries=0)
        self.addCleanup(self.dl.session.close)
        self.path = Path(self.tmp.name) / 'rom.zip'
        self.probe = ProbeResult('https://example.com/rom.zip', '', 'rom.zip', 3)
        self.dl.resolve_url = Mock(return_value=self.probe)

    def download(self, reply, **kwargs):
        with patch.object(self.dl.session, 'get', return_value=reply):
            return self.dl.download(self.probe.url, **kwargs)

    def test_download(self):
        self.assertTrue(self.download(response(headers={'Content-Length': '3'})))
        self.assertEqual(self.path.read_bytes(), b'abc')

    def test_resume(self):
        self.path.write_bytes(b'a')
        self.assertTrue(self.download(response(206, b'bc', {'Content-Range': 'bytes 1-2/3', 'Content-Length': '2'})))
        self.assertEqual(self.path.read_bytes(), b'abc')

    def test_ignored_range_restarts(self):
        self.path.write_bytes(b'a')
        self.assertTrue(self.download(response(headers={'Content-Length': '3'})))
        self.assertEqual(self.path.read_bytes(), b'abc')

    def test_bad_range_rejected(self):
        self.path.write_bytes(b'a')
        self.assertFalse(self.download(response(206, b'bc', {'Content-Range': 'bytes 0-1/3'})))
        self.assertEqual(self.path.read_bytes(), b'a')

    def test_416_is_failure(self):
        self.path.write_bytes(b'a')
        self.assertFalse(self.download(response(416)))

    def test_oversized_local_file_restarts(self):
        self.path.write_bytes(b'wrong data')
        self.assertTrue(self.download(response(headers={'Content-Length': '3'})))
        self.assertEqual(self.path.read_bytes(), b'abc')

    def test_short_body_fails(self):
        self.assertFalse(self.download(response(data=b'a', headers={'Content-Length': '3'})))

    def test_md5_mismatch_fails(self):
        self.probe.md5 = '0' * 32
        self.assertFalse(self.download(response(headers={'Content-Length': '3'})))

    def test_no_resume_retry(self):
        self.dl.retries = 1
        def attempt(probe, path, offset, resume):
            self.assertEqual(offset, 0)
            if not Path(path).exists():
                Path(path).write_bytes(b'a')
                raise requests.ConnectionError('interrupted')
            Path(path).write_bytes(b'abc')
            return True
        with patch.object(self.dl, '_do_download_attempt', side_effect=attempt):
            self.assertTrue(self.dl.download(self.probe.url, resume=False))

    def test_metadata_uses_ranges(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('META-INF/com/android/metadata', 'post-build-version=16\npost-sdk-level=36\n')
            z.writestr('payload.bin', b'x' * (5 * 1024 * 1024))
        data = buf.getvalue()
        self.probe.size = len(data)
        def get_range(url, start, end):
            self.assertLessEqual(end - start + 1, 4 * 1024 * 1024)
            return data[start:end+1]
        with patch.object(self.dl, '_range_get', side_effect=get_range):
            self.assertEqual(self.dl.fetch_metadata(self.probe.url)['android_version'], '16')

    def test_range_ignored_is_rejected(self):
        with patch.object(self.dl.session, 'get', return_value=response()):
            with self.assertRaises(ValueError):
                self.dl._range_get(self.probe.url, 1, 2)

    def test_unsafe_filename(self):
        dl = OtaDownloader(output_dir=self.tmp.name)
        self.addCleanup(dl.session.close)
        with patch.object(dl.session, 'head', return_value=response()), patch.object(dl.session, 'get', side_effect=lambda *a, **kw: response()):
            for name in ('../escape', '/tmp/escape', '..', 'a\\b'):
                with self.assertRaises(ValueError):
                    dl.resolve_url(self.probe.url, custom_name=name)

    def test_redirect_downgrade_rejected(self):
        r = response(302, headers={'Location': 'http://example.com/rom.zip'})
        with self.assertRaises(ValueError):
            SecureSession().get_redirect_target(r)

    def test_parallel_ranges(self):
        data = b'abcdefghijk'
        self.probe.size = len(data)
        seen = []
        def get(url, headers, **kwargs):
            start, end = map(int, headers['Range'].removeprefix('bytes=').split('-'))
            seen.append((start, end))
            return response(206, data[start:end+1], {'Content-Range': f'bytes {start}-{end}/{len(data)}'})
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get = get
        with patch.object(self.dl.session, 'get', side_effect=get), patch.object(self.dl, '_build_session', return_value=session):
            self.assertTrue(self.dl._parallel_download(self.probe, str(self.path)))
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(len(seen), 5)
        self.assertFalse(Path(str(self.path) + '.part').exists())

    def test_parallel_no_range_falls_back(self):
        with patch.object(self.dl.session, 'get', return_value=response()):
            self.assertFalse(self.dl._parallel_download(self.probe, str(self.path)))
        self.assertFalse(self.path.exists())

    def test_parallel_bad_range_preserves_destination(self):
        self.path.write_bytes(b'original')
        capability = response(206, b'a', {'Content-Range': 'bytes 0-0/3'})
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.side_effect = lambda *a, **k: response(206, b'bad', {'Content-Range': 'bytes 0-2/999'})
        with patch.object(self.dl.session, 'get', return_value=capability), patch.object(self.dl, '_build_session', return_value=session):
            with self.assertRaises(ValueError):
                self.dl._parallel_download(self.probe, str(self.path))
        self.assertEqual(self.path.read_bytes(), b'original')
        self.assertFalse(Path(str(self.path) + '.part').exists())


if __name__ == '__main__':
    unittest.main()
