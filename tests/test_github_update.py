import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request
from urllib.error import HTTPError
import zipfile

from ottv2.github_update import (repository_name, GitHubRedirects, fetch,
                                download_repository, update_from_github)
from ottv2.bot_store import import_zip, load_bot
from ottv2.sample_session import ROOT

SHA = 'a' * 40


class Response(BytesIO):
    status = 200
    headers = {}
    def geturl(self):
        return 'https://codeload.github.com/owner/repo/zip/' + SHA


def package(source):
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('repo-commit/main.py', source)
    return stream.getvalue()


class GitHubUpdateTests(unittest.TestCase):
    def setUp(self):
        from ottv2 import github_update
        github_update._ARCHIVES.clear()

    def test_authorization_is_api_only_and_is_removed_on_archive_redirect(self):
        from ottv2 import github_update
        with TemporaryDirectory() as tmp:
            token = Path(tmp)/'token'
            token.write_text('github_pat_'+'x'*40); token.chmod(0o600)
            opener = Mock(open=Mock(side_effect=lambda *a,**k:Response(b'ok')))
            with patch.dict('os.environ',{'OTTV2_GITHUB_TOKEN_FILE':str(token)}), patch.object(github_update,'build_opener',return_value=opener):
                fetch('https://api.github.com/test',10,'text/plain')
                self.assertTrue(opener.open.call_args.args[0].has_header('Authorization'))
                fetch('https://codeload.github.com/a/b/zip/'+SHA,10,'text/plain')
                self.assertFalse(opener.open.call_args.args[0].has_header('Authorization'))
            request = Request('https://api.github.com/test',headers={'Authorization':'Bearer placeholder'})
            redirected = GitHubRedirects().redirect_request(request,None,302,'',{},'https://codeload.github.com/a/b')
            self.assertFalse(redirected.has_header('Authorization'))

    def test_rate_limit_is_bounded_and_commit_cache_is_pinned(self):
        from ottv2 import github_update
        error = HTTPError('https://api.github.com/test',403,'limit',{'X-RateLimit-Remaining':'0','Retry-After':'600'},BytesIO())
        with patch('ottv2.github_update.time.time',return_value=1000), patch('ottv2.github_update.build_opener',return_value=Mock(open=Mock(side_effect=error))):
            with self.assertRaises(github_update.GitHubRateLimited) as raised:
                fetch('https://api.github.com/test',10,'text/plain')
        self.assertEqual(raised.exception.retry_at,1600)
        with patch('ottv2.github_update.fetch',side_effect=[SHA.encode(),b'ZIP',SHA.encode()]) as network:
            self.assertEqual(download_repository('https://github.com/a/b')[0],b'ZIP')
            self.assertEqual(download_repository('https://github.com/a/b')[0],b'ZIP')
            self.assertEqual(network.call_count,3) # Still check HEAD, reuse only immutable archive.

    def test_repository_urls_are_restricted(self):
        self.assertEqual(repository_name('https://github.com/owner/repo.git/'), 'owner/repo')
        for url in ('http://github.com/a/b', 'https://evil.example/a/b', 'https://github.com@127.0.0.1/a/b',
                    'https://github.com/a/b/tree/main', 'https://github.com/a/b?token=x',
                    'https://github.com/a/../', 'file:///tmp/repo', 'https://github.com/a/%2fetc'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                repository_name(url)

    def test_redirects_outside_github_or_without_https_are_blocked(self):
        request = Request('https://api.github.com/repos/a/b/zipball/' + SHA)
        for url in ('http://codeload.github.com/a/b', 'https://127.0.0.1/', 'https://evil.example/',
                    'https://codeload.github.com:444/a/b', 'https://user@api.github.com/a'):
            with self.assertRaises(ValueError):
                GitHubRedirects().redirect_request(request, None, 302, '', {}, url)
        handler = GitHubRedirects()
        for _ in range(3):
            self.assertIsNotNone(handler.redirect_request(request, None, 302, '', {},
                                                         'https://codeload.github.com/a/b'))
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, '', {}, 'https://codeload.github.com/a/b')

    def test_download_checks_declared_actual_and_incomplete_lengths(self):
        for headers, body in (({'Content-Length': '999'}, b'a'), ({}, b'x' * 11),
                              ({'Content-Length': '5'}, b'ab')):
            response = Response(body)
            response.headers = headers
            with patch('ottv2.github_update.build_opener', return_value=Mock(open=Mock(return_value=response))):
                with self.assertRaises(ValueError):
                    fetch('https://api.github.com/test', 10, 'application/octet-stream')
        response = Response(b'ok')
        with patch('ottv2.github_update.build_opener', return_value=Mock(open=Mock(return_value=response))):
            self.assertEqual(fetch('https://api.github.com/test', 10, 'application/octet-stream'), b'ok')

    def test_commit_is_pinned_before_zip_download(self):
        with patch('ottv2.github_update.fetch', side_effect=[SHA.encode(), b'ZIP']) as network:
            data, origin = download_repository('https://github.com/owner/repo')
        self.assertEqual(data, b'ZIP')
        self.assertEqual(origin['commit'], SHA)
        self.assertEqual(network.call_args_list[1].args[0], 'https://codeload.github.com/owner/repo/zip/' + SHA)
        with patch('ottv2.github_update.fetch', return_value=b'bad SHA'), self.assertRaises(ValueError):
            download_repository('https://github.com/owner/repo')

    def test_network_and_bad_zip_keep_current_unchanged(self):
        with TemporaryDirectory() as tmp:
            root, archive = Path(tmp) / 'store', Path(tmp) / 'good.zip'
            source = b'pass'
            archive.write_bytes(package(source))
            with patch('ottv2.bot_store.check_ready', return_value={
                    'ready': True, 'source_sha256': hashlib.sha256(source).hexdigest()}):
                import_zip(root, 'student01', archive)
            previous = (root / 'student01/current.json').read_bytes()
            with patch('ottv2.update_gate.time.time', return_value=1000), \
                    patch('ottv2.github_update.download_repository', side_effect=ValueError('network error')), \
                    self.assertRaises(ValueError):
                update_from_github(root, 'student01', 'https://github.com/owner/repo')
            with patch('ottv2.update_gate.time.time', return_value=1300), \
                    patch('ottv2.github_update.download_repository', return_value=(b'broken ZIP', {})), \
                    self.assertRaises(ValueError):
                update_from_github(root, 'student01', 'https://github.com/owner/repo')
            self.assertEqual((root / 'student01/current.json').read_bytes(), previous)
            self.assertEqual(load_bot(root, 'student01')[0], source)


@unittest.skipUnless(sys.platform.startswith('linux') and Path('/usr/local/bin/isolate').is_file(),
                     'requires configured Isolate')
class GitHubPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        from ottv2 import github_update
        github_update._ARCHIVES.clear()

    def test_download_pipeline_runs_real_ready_gate_and_records_origin(self):
        source = (ROOT / 'examples/greedy/main.py').read_bytes()
        origin = {'type': 'github', 'repository': 'https://github.com/owner/repo', 'commit': SHA}
        with TemporaryDirectory() as tmp, patch('ottv2.github_update.fetch', side_effect=[SHA.encode(), package(source)]):
            record = update_from_github(tmp, 'student01', origin['repository'])
            self.assertEqual(record['origin'], origin)
            self.assertIs(record['validation']['ready'], True)
            self.assertEqual(load_bot(tmp, 'student01')[0], source)


if __name__ == '__main__':
    unittest.main()
