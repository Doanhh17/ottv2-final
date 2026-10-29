import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

from ottv2.github_update import update_from_github
from ottv2.update_gate import accepted_update, update_status


class UpdateGateTests(unittest.TestCase):
    def test_old_thirty_minute_state_uses_new_three_minute_policy(self):
        with TemporaryDirectory() as tmp:
            with patch('ottv2.update_gate.time.time', return_value=1000), accepted_update(tmp, 'student01'):
                pass
            path = Path(tmp) / 'student01/update-request.json'
            old = json.loads(path.read_text())
            old['next_allowed_at'] = 2800
            path.write_text(json.dumps(old))
            original = path.read_bytes()
            with patch('ottv2.update_gate.time.time', return_value=1179):
                self.assertEqual(update_status(tmp, 'student01')['remaining_seconds'], 1)
            self.assertEqual(path.read_bytes(), original)
            with patch('ottv2.update_gate.time.time', return_value=1180), accepted_update(tmp, 'student01'):
                pass
            self.assertEqual(json.loads(path.read_text())['next_allowed_at'], 1360)

    def test_persistent_cooldown_rejects_before_boundary_without_extending_it(self):
        with TemporaryDirectory() as tmp:
            with patch('ottv2.update_gate.time.time', return_value=1000), accepted_update(tmp, 'student01'):
                pass
            path = Path(tmp) / 'student01/update-request.json'
            first = path.read_bytes()
            with patch('ottv2.update_gate.time.time', return_value=1179):
                self.assertEqual(update_status(tmp, 'student01')['remaining_seconds'], 1)
                with self.assertRaisesRegex(ValueError, 'cooldown'):
                    with accepted_update(tmp, 'student01'):
                        self.fail('request must not run')
            self.assertEqual(path.read_bytes(), first)
            with patch('ottv2.update_gate.time.time', return_value=1180), accepted_update(tmp, 'student01'):
                pass
            self.assertEqual(json.loads(path.read_bytes())['next_allowed_at'], 1360)

    def test_failed_accepted_request_counts_and_unlocks(self):
        with TemporaryDirectory() as tmp, patch('ottv2.update_gate.time.time', return_value=1000):
            network = Mock(side_effect=ValueError('download failed'))
            with patch('ottv2.github_update.download_repository', network):
                with self.assertRaisesRegex(ValueError, 'download failed'):
                    update_from_github(tmp, 'student01', 'https://github.com/owner/repo')
                with self.assertRaisesRegex(ValueError, 'cooldown'):
                    update_from_github(tmp, 'student01', 'https://github.com/owner/repo')
            self.assertEqual(network.call_count, 1)
            self.assertFalse((Path(tmp) / 'student01/.request.lock').exists())
            self.assertEqual(update_status(tmp, 'student01')['remaining_seconds'], 180)

    def test_simultaneous_request_is_blocked_but_other_bot_is_independent(self):
        with TemporaryDirectory() as tmp:
            entered, release = Event(), Event()
            errors = []
            def worker():
                try:
                    with accepted_update(tmp, 'student01'):
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError('test worker timed out')
                except Exception as error:
                    errors.append(error)
            thread = Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                previous = (Path(tmp) / 'student01/update-request.json').read_bytes()
                with self.assertRaisesRegex(ValueError, 'already running'):
                    with accepted_update(tmp, 'student01'):
                        self.fail('duplicate must not run')
                with accepted_update(tmp, 'student02'):
                    pass
                self.assertEqual((Path(tmp) / 'student01/update-request.json').read_bytes(), previous)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_state_write_failure_prevents_network_and_releases_lock(self):
        with TemporaryDirectory() as tmp, patch('ottv2.update_gate.replace_current', side_effect=OSError('disk error')), \
                patch('ottv2.github_update.download_repository') as network:
            with self.assertRaises(OSError):
                update_from_github(tmp, 'student01', 'https://github.com/owner/repo')
            network.assert_not_called()
            self.assertFalse((Path(tmp) / 'student01/.request.lock').exists())

    def test_corrupt_state_or_backwards_clock_is_not_reset(self):
        with TemporaryDirectory() as tmp:
            with patch('ottv2.update_gate.time.time', return_value=1000), accepted_update(tmp, 'student01'):
                pass
            path = Path(tmp) / 'student01/update-request.json'
            original = path.read_bytes()
            with patch('ottv2.update_gate.time.time', return_value=999), self.assertRaises(RuntimeError):
                with accepted_update(tmp, 'student01'):
                    pass
            self.assertEqual(path.read_bytes(), original)
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                with accepted_update(tmp, 'student01'):
                    pass
            self.assertEqual(path.read_text(), '{broken')

    def test_invalid_url_is_refused_before_acceptance_or_network(self):
        with TemporaryDirectory() as tmp, patch('ottv2.github_update.download_repository') as network:
            root = Path(tmp) / 'not-created'
            with self.assertRaises(ValueError):
                update_from_github(root, 'student01', 'http://127.0.0.1/repo')
            self.assertFalse(root.exists())
            network.assert_not_called()

    def test_ready_rejection_keeps_cooldown_and_current(self):
        with TemporaryDirectory() as tmp:
            with patch('ottv2.update_gate.time.time', return_value=1000), accepted_update(tmp, 'student01'):
                pass
            pointer = Path(tmp) / 'student01/current.json'
            pointer.write_bytes(b'old pointer unchanged')
            with patch('ottv2.update_gate.time.time', return_value=1180), \
                    patch('ottv2.github_update.download_repository', return_value=(b'candidate', {})), \
                    patch('ottv2.github_update.import_zip', side_effect=ValueError('READY failed')), \
                    self.assertRaisesRegex(ValueError, 'READY failed'):
                update_from_github(tmp, 'student01', 'https://github.com/owner/repo')
            self.assertEqual(pointer.read_bytes(), b'old pointer unchanged')
            with patch('ottv2.update_gate.time.time', return_value=1180):
                self.assertEqual(update_status(tmp, 'student01')['remaining_seconds'], 180)


if __name__ == '__main__':
    unittest.main()
