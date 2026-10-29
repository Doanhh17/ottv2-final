from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ottv2 import accounts


class AccountTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.queue = self.root / 'queue.sqlite3'
        self.queue.touch()
        self.db = self.root / 'accounts.sqlite3'
        # HTTP tests cover the real configured iteration count too.
        rounds = patch.object(accounts, 'ROUNDS', 1000)
        rounds.start()
        self.addCleanup(rounds.stop)
        accounts.initialize(self.db, self.queue)
        accounts.create_user(self.db, 'student01', 'correct-password-123')

    def test_password_not_stored_plaintext_and_sessions_expire_and_revoke(self):
        token, user = accounts.login(self.db, 'student01', 'correct-password-123', now=1000)
        self.assertEqual(user['identity'], 'student01')
        raw = self.db.read_bytes()
        self.assertNotIn(b'correct-password-123', raw)
        self.assertNotIn(token.encode(), raw)
        self.assertEqual(accounts.session(self.db, token, now=1001), user)
        self.assertIsNone(accounts.session(self.db, token, now=1000 + accounts.SESSION_SECONDS))
        accounts.logout(self.db, token)
        self.assertIsNone(accounts.session(self.db, token, now=1001))

    def test_reset_revokes_all_sessions_and_old_password(self):
        first, _ = accounts.login(self.db, 'student01', 'correct-password-123')
        with self.assertRaisesRegex(ValueError, 'Đang đăng nhập ở chỗ khác'):
            accounts.login(self.db, 'student01', 'correct-password-123')
        accounts.change_password(self.db, 'student01', 'new-password-123')
        for token in (first,):
            self.assertIsNone(accounts.session(self.db, token))
        with self.assertRaises(ValueError):
            accounts.login(self.db, 'student01', 'correct-password-123')
        self.assertTrue(accounts.login(self.db, 'student01', 'new-password-123'))

    def test_failed_logins_are_persistently_limited_without_disclosing_identity(self):
        for identity in ('student01', 'not-a-user'):
            messages = []
            for _ in range(10):
                with self.assertRaises(ValueError) as result:
                    accounts.login(self.db, identity, 'incorrect-password', now=1000)
                messages.append(str(result.exception))
            self.assertEqual(len(set(messages)), 1)
            with self.assertRaises(accounts.LoginLimited):
                accounts.login(self.db, identity, 'correct-password-123', now=1100)
        self.assertTrue(accounts.login(self.db, 'student01', 'correct-password-123', now=1301))

    def test_identity_validation_duplicate_and_initialization_preserve_data(self):
        before = self.db.read_bytes()
        accounts.initialize(self.db, self.queue)
        with self.assertRaises(ValueError):
            accounts.create_user(self.db, 'student01', 'different-password')
        with self.assertRaises(ValueError):
            accounts.initialize(self.db, self.root / 'other.sqlite3')
        with self.assertRaises(ValueError):
            accounts.create_user(self.db, '../student', 'correct-password-123')
        with self.assertRaises(ValueError):
            accounts.create_user(self.db, 'student02', 'short')
        self.assertEqual(before, self.db.read_bytes())

    def test_successful_users_do_not_exhaust_shared_failure_budget(self):
        for _ in range(105):
            token, _ = accounts.login(self.db, 'student01', 'correct-password-123', now=1000)
            accounts.logout(self.db, token)
        with accounts.database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count FROM attempts WHERE key='global'").fetchone()[0], 0)
        for number in range(100):
            with self.assertRaises(ValueError):
                accounts.login(self.db, 'missing'+str(number), 'incorrect-password', now=1000)
        with self.assertRaises(accounts.LoginLimited):
            accounts.login(self.db, 'student01', 'correct-password-123', now=1000)

    def test_session_limit_and_repository_validation(self):
        token,_ = accounts.login(self.db, 'student01', 'correct-password-123')
        with self.assertRaisesRegex(ValueError, 'Đang đăng nhập ở chỗ khác'):
            accounts.login(self.db, 'student01', 'correct-password-123')
        with self.assertRaises(ValueError):
            accounts.set_repository(self.db, 'student01', 'http://localhost/private')
        accounts.set_repository(self.db, 'student01', 'https://github.com/student/bot.git')
        self.assertEqual(accounts.session(self.db, token)['repository'], 'https://github.com/student/bot')

    def test_student_expiry_waits_seven_minutes_but_early_logout_does_not(self):
        token,_=accounts.login(self.db,'student01','correct-password-123',now=1000)
        self.assertIsNotNone(accounts.session(self.db,token,now=2799))
        self.assertIsNone(accounts.session(self.db,token,now=2800))
        with self.assertRaisesRegex(accounts.LoginLimited,'7 phút'):
            accounts.login(self.db,'student01','correct-password-123',now=2800)
        with self.assertRaises(accounts.LoginLimited):
            accounts.login(self.db,'student01','correct-password-123',now=3219)
        token,_=accounts.login(self.db,'student01','correct-password-123',now=3220)
        accounts.logout(self.db,token,now=3230)
        self.assertTrue(accounts.login(self.db,'student01','correct-password-123',now=3231))

    def test_admin_has_one_thirty_minute_session_without_relogin_wait(self):
        accounts.create_user(self.db,'admin','admin-password-123',role='admin')
        token,_=accounts.login(self.db,'admin','admin-password-123',now=1000)
        with self.assertRaisesRegex(ValueError,'Đang đăng nhập ở chỗ khác'):
            accounts.login(self.db,'admin','admin-password-123',now=1001)
        self.assertIsNone(accounts.session(self.db,token,now=2800))
        self.assertTrue(accounts.login(self.db,'admin','admin-password-123',now=2800))

    def test_parallel_correct_logins_create_only_one_session(self):
        from concurrent.futures import ThreadPoolExecutor
        def attempt(_):
            try:return accounts.login(self.db,'student01','correct-password-123',now=1000)[0]
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(token is not None for token in results),1)
        with accounts.database(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],1)
