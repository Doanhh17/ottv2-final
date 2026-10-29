import hashlib
from pathlib import Path
import json
from tempfile import TemporaryDirectory
import unittest

from ottv2 import accounts, bot_store, match_queue, portal_data
from scripts.load_test_server import seed, seed_history


class LoadFixtureTests(unittest.TestCase):
    def test_distinct_synthetic_sessions_authenticate_only_their_own_users(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)/'fixture'
            seed(root,4)
            fixture=json.loads((root/'clients.private.json').read_text())
            self.assertTrue(fixture['synthetic'])
            self.assertEqual(len({c['cookie'] for c in fixture['clients']}),4)
            for client in fixture['clients']:
                token=client['cookie'].partition('=')[2]
                user=accounts.session(root/'accounts.sqlite3',token)
                self.assertEqual(user['identity'],client['identity'])
                self.assertEqual(user['csrf'],client['csrf'])
                source,metadata=bot_store.load_bot(root/'bots',client['identity'])
                self.assertEqual(metadata['version'],hashlib.sha256(source).hexdigest())
            first=fixture['clients'][0]
            with self.assertRaisesRegex(ValueError, 'Đang đăng nhập ở chỗ khác'):
                accounts.login(root/'accounts.sqlite3',first['identity'],fixture['password'])
            previous=first['cookie'].partition('=')[2]
            accounts.logout(root/'accounts.sqlite3',previous)
            self.assertIsNone(accounts.session(root/'accounts.sqlite3',previous))
            token,user=accounts.login(root/'accounts.sqlite3',first['identity'],fixture['password'])
            self.assertEqual(user['identity'],fixture['clients'][0]['identity'])
            self.assertIsNotNone(accounts.session(root/'accounts.sqlite3',token))

    def test_existing_directory_and_out_of_range_count_are_rejected_without_writes(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            sentinel=root/'keep';sentinel.write_text('preserve')
            for count in (0,1,4001):
                with self.assertRaises(ValueError):seed(root/'invalid',count)
                self.assertFalse((root/'invalid').exists())
            with self.assertRaises(ValueError):seed(root,4)
            self.assertEqual(list(root.iterdir()),[sentinel])

    def test_history_fixture_keeps_real_api_ownership_and_is_not_seeded_twice(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)/'fixture';seed(root,4)
            for i in range(1,5):match_queue.find_match(root/'queue.sqlite3',f'load{i:05d}')
            result=seed_history(root,3)
            self.assertEqual(result['completed_fixture_jobs'],6)
            rows=portal_data.history(root/'queue.sqlite3',root/'matches','load00001')
            completed=[r for r in rows if r['state']=='completed']
            self.assertEqual(len(completed),3)
            self.assertTrue(all(r['log_available'] for r in completed))
            with self.assertRaises(FileNotFoundError):
                portal_data.own_log(root/'queue.sqlite3',root/'matches','load00003',completed[0]['match_id'])
            with self.assertRaises(ValueError):seed_history(root,3)


if __name__=='__main__':unittest.main()
