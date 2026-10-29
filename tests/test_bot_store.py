import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from ottv2.bot_store import import_zip, load_bot, IsolatedStoredSession
from ottv2.cpu_trial import run_trial
from ottv2.isolated_session import BOX_ROOT
from ottv2.sample_session import ROOT


def archive(path, source):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('main.py', source)


class BotStoreTests(unittest.TestCase):
    def setUp(self):
        # Storage unit tests stay independent of the Linux sandbox. Integration
        # tests below and in test_ready_check exercise the real READY gate.
        probe = patch('ottv2.bot_store.check_ready', side_effect=lambda source:
                      {'ready': True, 'source_sha256': hashlib.sha256(source).hexdigest()})
        probe.start()
        self.addCleanup(probe.stop)

    def test_versions_retained_duplicate_import_and_corruption_detected(self):
        with TemporaryDirectory() as tmp:
            root, path = Path(tmp) / 'store', Path(tmp) / 'bot.zip'
            archive(path, b'pass\n')
            first = import_zip(root, 'student01', path)
            self.assertEqual(import_zip(root, 'student01', path)['version'], first['version'])
            archive(path, b'# version2\npass\n')
            second = import_zip(root, 'student01', path)
            self.assertNotEqual(first['version'], second['version'])
            self.assertTrue((root / 'student01/versions' / (first['version'] + '.py')).is_file())
            source, current = load_bot(root, 'student01')
            self.assertEqual(source, b'# version2\npass\n')
            self.assertEqual(current['version'], second['version'])
            (root / 'student01/versions' / (second['version'] + '.py')).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                load_bot(root, 'student01')
            with self.assertRaisesRegex(ValueError, 'corrupt'):
                import_zip(root, 'student01', path)

    def test_bad_zip_and_failed_pointer_replace_keep_previous_bot(self):
        with TemporaryDirectory() as tmp:
            root, path = Path(tmp) / 'store', Path(tmp) / 'bot.zip'
            archive(path, b'pass')
            import_zip(root, 'student01', path)
            pointer = root / 'student01/current.json'
            previous = pointer.read_bytes()
            path.write_bytes(b'bad ZIP')
            with self.assertRaises(ValueError):
                import_zip(root, 'student01', path)
            self.assertEqual(pointer.read_bytes(), previous)
            archive(path, b'pass #new')
            with patch('ottv2.bot_store.os.replace', side_effect=OSError('disk failure')), self.assertRaises(OSError):
                import_zip(root, 'student01', path)
            self.assertEqual(pointer.read_bytes(), previous)
            self.assertEqual(load_bot(root, 'student01')[0], b'pass')
            self.assertFalse((root / 'student01/.update.lock').exists())
            self.assertFalse(list((root / 'student01').glob('.current-*')))

    def test_lock_and_bot_id_validation(self):
        with TemporaryDirectory() as tmp:
            root, path = Path(tmp) / 'store', Path(tmp) / 'bot.zip'
            archive(path, b'pass')
            for bot_id in ('../bad', 'A', 'con', 'a/b', '', 'x' * 41):
                with self.assertRaises(ValueError):
                    import_zip(root, bot_id, path)
            import_zip(root, 'student01', path)
            lock = root / 'student01/.update.lock'
            lock.write_text('other updater')
            with self.assertRaisesRegex(RuntimeError, 'locked'):
                import_zip(root, 'student01', path)
            self.assertEqual(lock.read_text(), 'other updater')

    def test_session_pins_source_even_if_current_changes(self):
        with TemporaryDirectory() as tmp:
            root, path = Path(tmp) / 'store', Path(tmp) / 'bot.zip'
            archive(path, b'pass #old')
            first = import_zip(root, 'student01', path)
            session = IsolatedStoredSession(root, 'student01', 0)
            archive(path, b'pass #new')
            import_zip(root, 'student01', path)
            self.assertEqual(session.source, b'pass #old')
            self.assertEqual(session.source_info['version'], first['version'])
            self.assertIsNone(session.process)

    def test_invalid_pointer_cannot_reference_outside_store(self):
        with TemporaryDirectory() as tmp:
            root, path = Path(tmp) / 'store', Path(tmp) / 'bot.zip'
            archive(path, b'pass')
            import_zip(root, 'student01', path)
            pointer = root / 'student01/current.json'
            metadata = json.loads(pointer.read_text())
            metadata['version'] = '../../outside'
            pointer.write_text(json.dumps(metadata))
            with self.assertRaises(ValueError):
                load_bot(root, 'student01')
            with patch('ottv2.cpu_trial.sys.platform', 'linux'), patch('ottv2.isolated_session.checked') as command:
                with self.assertRaises(ValueError):
                    run_trial(bot_store=root, bot_a_current='student01')
                command.assert_not_called()


@unittest.skipUnless(sys.platform.startswith('linux') and Path('/usr/local/bin/isolate').is_file(),
                     'requires configured Isolate')
class StoredBotIntegrationTests(unittest.TestCase):
    def test_saved_bot_runs_in_sandbox_and_log_pins_version(self):
        with TemporaryDirectory() as tmp:
            root, path, result = Path(tmp) / 'store', Path(tmp) / 'bot.zip', Path(tmp) / 'match.json'
            source = (ROOT / 'examples/greedy/main.py').read_bytes()
            archive(path, source)
            metadata = import_zip(root, 'student01', path)
            game, _, _ = run_trial(bot_store=root, bot_a_current='student01',
                                   bot_b_file=ROOT / 'examples/greedy/main.py', result_file=result,
                                   emit=lambda _: None)
            record = json.loads(result.read_text())
            self.assertIsNotNone(game.result)
            self.assertEqual(record['bots'], {'A': 'stored', 'B': 'file'})
            self.assertEqual(record['bot_sources']['A']['version'], metadata['version'])
            self.assertEqual(record['bot_sources']['A']['sha256'], hashlib.sha256(source).hexdigest())
            self.assertTrue(record['cleanup_complete'])
        self.assertFalse(any((BOX_ROOT / str(i)).exists() for i in (0, 1)))


if __name__ == '__main__':
    unittest.main()
