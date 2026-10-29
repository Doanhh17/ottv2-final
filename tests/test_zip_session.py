import hashlib
import json
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings
import zipfile

from ottv2.cpu_trial import run_trial
from ottv2.zip_session import IsolatedZipSession, read_submission_zip
from ottv2.isolated_session import BOX_ROOT
from ottv2.sample_session import ROOT


def make_zip(path, entries, compression=zipfile.ZIP_DEFLATED):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'w', compression=compression) as archive:
            for name, data in entries:
                archive.writestr(name, data)


class ZipInputTests(unittest.TestCase):
    def test_plain_and_github_wrapper_snapshot_only_main_py(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bot.zip'
            for prefix in ('', 'repo-commit/'):
                make_zip(path, [(prefix + 'main.py', b'pass\n'), (prefix + 'README.md', b'documentation')])
                source, meta = read_submission_zip(path)
                self.assertEqual(source, b'pass\n')
                self.assertEqual(meta['archive_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
                session = IsolatedZipSession(path, 0)
                path.write_bytes(b'changed after snapshot')
                self.assertEqual(session.source, b'pass\n')
                self.assertIsNone(session.process)
                self.assertEqual(session.source_info['sha256'], hashlib.sha256(b'pass\n').hexdigest())

    def test_unsafe_names_duplicates_and_extra_python_are_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bot.zip'
            for name in ('../outside.txt', '/outside.txt', 'C:/outside.txt', 'a\\b.txt',
                         'a/../b.txt', './readme.txt', 'a//b.txt', 'main.py', 'MAIN.PY',
                         'helper.py', 'sub/helper.py', 'cache.pyc'):
                with self.subTest(name=name):
                    # Windows ZipInfo normalizes backslashes while writing, so
                    # construct that hostile name in both raw ZIP headers.
                    written = name.replace('\\', '_')
                    make_zip(path, [('main.py', b'pass'), (written, b'pass')])
                    if written != name:
                        path.write_bytes(path.read_bytes().replace(written.encode(), name.encode()))
                    with self.assertRaises(ValueError):
                        read_submission_zip(path)

    def test_missing_empty_or_nested_entrypoint_is_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bot.zip'
            for entries in ([], [('main.py', b'')], [('repo/src/main.py', b'pass')],
                            [('one/main.py', b'pass'), ('two/README.md', b'text')]):
                make_zip(path, entries)
                with self.assertRaises(ValueError):
                    read_submission_zip(path)

    def test_symlink_and_encrypted_or_corrupt_archives_are_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bot.zip'
            link = zipfile.ZipInfo('link')
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            make_zip(path, [('main.py', b'pass'), (link, b'/etc/passwd')])
            with self.assertRaises(ValueError):
                read_submission_zip(path)
            make_zip(path, [('main.py', b'pass')], compression=zipfile.ZIP_STORED)
            original = path.read_bytes()
            corrupt = bytearray(original)
            at = corrupt.index(b'pass')
            corrupt[at] ^= 1
            path.write_bytes(corrupt)
            with self.assertRaises(ValueError):
                read_submission_zip(path)
            encrypted = bytearray(original)
            cd = encrypted.index(b'PK\x01\x02')
            encrypted[cd + 8] |= 1
            path.write_bytes(encrypted)
            with self.assertRaises(ValueError):
                read_submission_zip(path)
            path.write_bytes(b'not a ZIP')
            with self.assertRaises(ValueError):
                read_submission_zip(path)

    def test_compressed_unpacked_source_and_entry_limits(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bot.zip'
            make_zip(path, [('main.py', b'x' * 100)])
            for constant in ('MAX_ZIP_BYTES', 'MAX_UNPACKED_BYTES', 'MAX_SOURCE_BYTES'):
                with patch('ottv2.zip_session.' + constant, 10), self.assertRaises(ValueError):
                    read_submission_zip(path)
            make_zip(path, [(str(i) + '.txt', b'') for i in range(257)])
            with self.assertRaisesRegex(ValueError, '256'):
                read_submission_zip(path)

    def test_invalid_second_zip_starts_no_sandbox(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.zip'
            path.write_bytes(b'bad')
            with patch('ottv2.cpu_trial.sys.platform', 'linux'), patch('ottv2.isolated_session.checked') as command:
                with self.assertRaises(ValueError):
                    run_trial(bot_a_file=ROOT / 'examples/greedy/main.py', bot_b_zip=path)
                command.assert_not_called()


@unittest.skipUnless(sys.platform.startswith('linux') and Path('/usr/local/bin/isolate').is_file(),
                     'requires configured Isolate')
class ZipIntegrationTests(unittest.TestCase):
    def test_zip_runs_in_sandbox_and_logs_both_hashes(self):
        with TemporaryDirectory() as tmp:
            path, result = Path(tmp) / 'bot.zip', Path(tmp) / 'match.json'
            source = (ROOT / 'examples/greedy/main.py').read_bytes()
            make_zip(path, [('repo/main.py', source), ('repo/README.md', b'ignored')])
            game, _, _ = run_trial(bot_a_zip=path, bot_b_file=ROOT / 'examples/greedy/main.py',
                                   result_file=result, emit=lambda _: None)
            record = json.loads(result.read_text())
            self.assertIsNotNone(game.result)
            self.assertEqual(record['bots'], {'A': 'zip', 'B': 'file'})
            self.assertEqual(record['bot_sources']['A']['sha256'], hashlib.sha256(source).hexdigest())
            self.assertEqual(record['bot_sources']['A']['archive_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(record['illegal_attempts'], {'A': 0, 'B': 0})
            self.assertTrue(record['cleanup_complete'])
        self.assertFalse(any((BOX_ROOT / str(i)).exists() for i in (0, 1)))


if __name__ == '__main__':
    unittest.main()
