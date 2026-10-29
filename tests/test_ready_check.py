import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from ottv2.bot_store import import_zip, load_bot
from ottv2.isolated_session import BOX_ROOT
from ottv2.ready_check import check_ready
from ottv2.sample_session import ROOT

READY = b'import json,sys\nprint(json.dumps({"protocol_version":1,"type":"ready"}),flush=True)\n'


def archive(path, source):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('main.py', source)


class ReadyGateUnitTests(unittest.TestCase):
    def test_probe_failure_keeps_current_and_publishes_no_new_version(self):
        with TemporaryDirectory() as tmp:
            root, path = Path(tmp) / 'store', Path(tmp) / 'bot.zip'
            source = b'old snapshot'
            archive(path, source)
            with patch('ottv2.bot_store.check_ready', return_value={
                    'ready': True, 'source_sha256': hashlib.sha256(source).hexdigest()}):
                import_zip(root, 'student01', path)
            previous = (root / 'student01/current.json').read_bytes()
            archive(path, b'new snapshot')
            for error in (ValueError('candidate failed'), RuntimeError('sandbox unavailable')):
                with patch('ottv2.bot_store.check_ready', side_effect=error), self.assertRaises(type(error)):
                    import_zip(root, 'student01', path)
                self.assertEqual((root / 'student01/current.json').read_bytes(), previous)
                self.assertEqual(load_bot(root, 'student01')[0], source)
                self.assertEqual(len(list((root / 'student01/versions').glob('*.py'))), 1)
                self.assertFalse((root / 'student01/.update.lock').exists())

    def test_legacy_unvalidated_pointer_requires_reimport(self):
        with TemporaryDirectory() as tmp:
            root, path = Path(tmp) / 'store', Path(tmp) / 'bot.zip'
            source = b'pass'
            archive(path, source)
            with patch('ottv2.bot_store.check_ready', return_value={
                    'ready': True, 'source_sha256': hashlib.sha256(source).hexdigest()}):
                import_zip(root, 'student01', path)
            pointer = root / 'student01/current.json'
            obj = json.loads(pointer.read_text())
            del obj['validation']
            pointer.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, 'import its ZIP again'):
                load_bot(root, 'student01')

    def test_occupied_box_is_not_touched(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '1').mkdir()
            sentinel = root / '1/keep'
            sentinel.write_text('running match')
            with patch('ottv2.ready_check.sys.platform', 'linux'), \
                    patch('ottv2.ready_check.BOX_ROOT', root), patch('ottv2.ready_check.ReadySession') as session:
                with self.assertRaisesRegex(RuntimeError, 'using the sandbox'):
                    check_ready(READY)
            session.assert_not_called()
            self.assertEqual(sentinel.read_text(), 'running match')


@unittest.skipUnless(sys.platform.startswith('linux') and Path('/usr/local/bin/isolate').is_file(),
                     'requires configured Isolate')
class ReadyGateIntegrationTests(unittest.TestCase):
    def assert_clean(self):
        self.assertFalse(any((BOX_ROOT / str(i)).exists() for i in (0, 1)))

    def test_ready_succeeds_without_sending_a_board_even_for_pondering_bot(self):
        sources = ((ROOT / 'examples/greedy/main.py').read_bytes(), READY + b'while True: pass\n')
        for source in sources:
            validation = check_ready(source)
            self.assertIs(validation['ready'], True)
            self.assertEqual(validation['source_sha256'], hashlib.sha256(source).hexdigest())
            self.assertEqual(validation['memory_limit_mib'], 64)
            self.assertEqual(validation['startup_limit_seconds'], 5)
            self.assert_clean()

    def test_failed_candidates_keep_a_real_checked_bot(self):
        with TemporaryDirectory() as tmp:
            root, path = Path(tmp) / 'store', Path(tmp) / 'bot.zip'
            archive(path, (ROOT / 'examples/greedy/main.py').read_bytes())
            first = import_zip(root, 'student01', path)
            pointer = root / 'student01/current.json'
            previous = pointer.read_bytes()
            cases = [(b'def bad(:', 'startup_runtime_error'),
                     (b'print("{}",flush=True)\ninput()\n', 'startup_format_error'),
                     (b'import time\ntime.sleep(10)\n', 'startup_timeout'),
                     (b'blocks=[]\nwhile True: blocks.append(bytearray(4*1024*1024))\n', 'startup_memory_limit')]
            for source, reason in cases:
                with self.subTest(reason=reason):
                    archive(path, source)
                    with self.assertRaisesRegex(ValueError, reason):
                        import_zip(root, 'student01', path)
                    self.assertEqual(pointer.read_bytes(), previous)
                    self.assertEqual(load_bot(root, 'student01')[1]['version'], first['version'])
                    self.assertFalse((root / 'student01/.update.lock').exists())
                    self.assert_clean()


if __name__ == '__main__':
    unittest.main()
