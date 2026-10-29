import io
import json
import tarfile
import unittest
from unittest.mock import patch

from ottv2 import accounts, backup, daily_quota, elo_store, local_worker, match_queue, portal_data
from tests.test_local_worker import WorkerFixture


class BackupTests(WorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.archive = self.root.parent / (self.root.name + '-snapshot.tar.gz')
        self.addCleanup(lambda: self.archive.unlink(missing_ok=True))
        self.restored = self.root / 'restored'
        self.account_db = self.root / 'accounts.sqlite3'
        self.rounds = patch.object(accounts, 'ROUNDS', 1000)
        self.rounds.start()
        self.addCleanup(self.rounds.stop)
        accounts.initialize(self.account_db, self.db)
        accounts.create_user(self.account_db, 'student01', 'private-password-123')

    def test_restore_preserves_accounts_bots_result_ratings_quota_and_relocates_paths(self):
        local_worker.run_once(self.db, self.results, self.fake_runner)
        token, _ = accounts.login(self.account_db, 'student01', 'private-password-123')
        # A private bridge configuration is included, never printed in the receipt.
        (self.root/'google-client.json').write_text('{"example":"private"}')
        receipt = backup.create(self.root, self.archive)
        self.assertNotIn('private', json.dumps(receipt))
        self.assertEqual(backup.restore(self.archive, self.restored)['state'], 'backup_restored')
        restored_queue = self.restored/'queue.sqlite3'
        self.assertEqual(portal_data.config(restored_queue)['store'], str(self.restored/'bots'))
        self.assertEqual(elo_store.standings(self.restored/'elo.sqlite3'), elo_store.standings(self.elo))
        self.assertEqual(daily_quota.status(restored_queue, 'student01'), daily_quota.status(self.db, 'student01'))
        job = match_queue.get_job(restored_queue, self.match_id)
        self.assertEqual(job['result_path'], str(self.restored/'matches'/(self.match_id+'.json')))
        self.assertTrue(portal_data.own_log(restored_queue, self.restored/'matches', 'student01', self.match_id))
        self.assertIsNone(accounts.session(self.restored/'accounts.sqlite3', token))
        self.assertTrue(accounts.login(self.restored/'accounts.sqlite3', 'student01', 'private-password-123'))
        self.assertTrue(accounts.session(self.account_db, token))
        self.assertEqual((self.restored/'google-client.json').read_bytes(), (self.root/'google-client.json').read_bytes())

    def test_existing_backup_or_restore_target_is_never_overwritten(self):
        backup.create(self.root, self.archive)
        before = self.archive.read_bytes()
        with self.assertRaises(ValueError): backup.create(self.root, self.archive)
        self.assertEqual(self.archive.read_bytes(), before)
        with self.assertRaises(ValueError): backup.restore(self.archive, self.root)
        with self.assertRaises(ValueError): backup.create(self.root, self.root/'inside.tar.gz')

    def test_path_traversal_and_links_in_archive_are_rejected_before_restore(self):
        for name, kind in (('../escaped', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)):
            with tarfile.open(self.archive, 'w:gz') as stream:
                entry=tarfile.TarInfo(name); entry.type=kind; entry.linkname='../outside'
                stream.addfile(entry, io.BytesIO())
            with self.assertRaises(ValueError): backup.restore(self.archive, self.restored)
            self.assertFalse(self.restored.exists())

    def test_changed_content_fails_checksum_and_leaves_no_destination(self):
        backup.create(self.root, self.archive)
        with tarfile.open(self.archive, 'r:gz') as stream:
            content={m.name:stream.extractfile(m).read() for m in stream.getmembers()}
        content['queue.sqlite3'] += b'tampered'
        with tarfile.open(self.archive, 'w:gz') as stream:
            for name,data in content.items():
                entry=tarfile.TarInfo(name); entry.size=len(data)
                stream.addfile(entry, io.BytesIO(data))
        with self.assertRaisesRegex(ValueError, 'checksum'): backup.restore(self.archive, self.restored)
        self.assertFalse(self.restored.exists())


if __name__ == '__main__': unittest.main()
