from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import patch

from ottv2 import daily_quota, elo_store, local_runner, local_worker, log_retention, match_queue, portal_data
from tests.test_local_worker import WorkerFixture


class RetentionTests(WorkerFixture, unittest.TestCase):
    def test_list_uses_committed_summary_without_reading_replay(self):
        self.complete()
        with patch('ottv2.portal_data.own_log',side_effect=AssertionError('no replay parsing')):
            row = portal_data.history(self.db,self.results,'student01')[0]
        self.assertTrue(row['log_available'])
        self.assertIsNotNone(row['result'])

    def complete(self):
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.path = self.results / (self.match_id + '.json')
        with closing(elo_store._connect(self.db, readonly=True)) as db:
            self.saved = log_retention.metadata(db, self.match_id)
        return self.saved['expires_at']

    def test_preview_is_read_only_and_exact_seven_day_boundary(self):
        expiry = self.complete()
        before = self.db.read_bytes(), self.path.read_bytes(), self.elo.read_bytes()
        self.assertEqual(log_retention.prune(self.db, self.results, now=expiry-0.001)['eligible'],0)
        preview=log_retention.prune(self.db,self.results,now=expiry)
        self.assertEqual(preview['eligible'],1)
        self.assertEqual(preview['deleted'],0)
        self.assertEqual((self.db.read_bytes(),self.path.read_bytes(),self.elo.read_bytes()),before)
        self.assertEqual(self.saved['expires_at']-self.saved['completed_at'],7*24*3600)

    def test_delete_preserves_elo_quota_receipt_and_visible_result(self):
        expiry=self.complete()
        ledger=self.elo.read_bytes()
        quotas={i:daily_quota.status(self.db,i) for i in ('student01','student02')}
        job=match_queue.get_job(self.db,self.match_id)
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['deleted'],1)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.elo.read_bytes(),ledger)
        self.assertEqual(match_queue.get_job(self.db,self.match_id),job)
        for identity,quota in quotas.items(): self.assertEqual(daily_quota.status(self.db,identity),quota)
        row=portal_data.history(self.db,self.results,'student01')[0]
        self.assertTrue(row['log_expired'])
        self.assertFalse(row['log_available'])
        self.assertEqual(row['result']['winner'],job['elo_receipt']['winner'])
        with self.assertRaises(log_retention.LogExpired):
            portal_data.own_log(self.db,self.results,'student01',self.match_id)
        with self.assertRaises(FileNotFoundError) as error:
            portal_data.own_log(self.db,self.results,'stranger',self.match_id)
        self.assertNotIsInstance(error.exception,log_retention.LogExpired)
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry+1)['deleted'],0)

    def test_pending_running_and_unmanaged_files_are_preserved(self):
        self.results.mkdir()
        path=self.results/(self.match_id+'.json')
        path.write_text('needed to recover')
        extra=self.results/('f'*32+'.json');extra.write_text('unmanaged')
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=time.time()+1e7)['checked'],0)
        with match_queue._write(self.db) as db:
            db.execute("UPDATE jobs SET state='running',result_path=? WHERE match_id=?",(str(path),self.match_id))
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=time.time()+1e7)['checked'],0)
        self.assertEqual(path.read_text(),'needed to recover')
        self.assertEqual(extra.read_text(),'unmanaged')

    def test_failed_and_cancelled_jobs_never_become_cleanup_candidates(self):
        for state in ('failed','cancelled'):
            with match_queue._write(self.db) as db:
                db.execute('UPDATE jobs SET state=? WHERE match_id=?',(state,self.match_id))
            self.assertEqual(log_retention.prune(self.db,self.results,apply=True)['checked'],0)

    def test_corrupt_or_replaced_log_is_kept(self):
        expiry=self.complete()
        original=self.path.read_bytes()
        self.path.write_text('broken json')
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['skipped'],1)
        self.assertEqual(self.path.read_text(),'broken json')
        record=json.loads(original)
        record['created_at_utc']='2020-01-01T00:00:00+00:00'
        self.path.write_text(json.dumps(record))
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['skipped'],1)
        self.assertTrue(self.path.exists())
        self.path.write_bytes(original)
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['deleted'],1)

    def test_unlink_failure_retries_without_losing_result(self):
        expiry=self.complete()
        with patch.object(Path,'unlink',side_effect=PermissionError('busy')):
            self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['skipped'],1)
        self.assertTrue(self.path.exists())
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry+1)['deleted'],1)
        self.assertTrue(portal_data.history(self.db,self.results,'student01')[0]['result'])

    def test_restart_after_file_deletion_before_commit_finishes_safely(self):
        expiry=self.complete()
        self.path.unlink()  # Simulate the crash window before recording deletion.
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['deleted'],1)
        with closing(elo_store._connect(self.db,readonly=True)) as db:
            self.assertEqual(log_retention.metadata(db,self.match_id)['deleted_at'],expiry)

    def test_legacy_completed_queue_uses_later_file_time_and_backfills_summary(self):
        self.complete()
        with match_queue._write(self.db) as db:
            db.execute('DROP TABLE replay_retention')
        stamp=time.time()
        os.utime(self.path,(stamp,stamp))
        before=self.db.read_bytes()
        self.assertEqual(log_retention.prune(self.db,self.results,now=stamp+log_retention.KEEP_SECONDS-1)['eligible'],0)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=stamp+log_retention.KEEP_SECONDS)['deleted'],1)
        row=portal_data.history(self.db,self.results,'student01')[0]
        self.assertIsInstance(row['result']['valid_move_count'],int)

    def test_wrong_saved_result_path_never_deletes_another_file(self):
        expiry=self.complete()
        outside=self.root/'keep.json';outside.write_bytes(self.path.read_bytes())
        with match_queue._write(self.db) as db:
            db.execute('UPDATE jobs SET result_path=? WHERE match_id=?',(str(outside),self.match_id))
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['skipped'],1)
        self.assertTrue(outside.exists());self.assertTrue(self.path.exists())

    @unittest.skipUnless(sys.platform.startswith('linux'),'symlink test requires Linux')
    def test_symlink_file_and_parent_are_never_followed(self):
        expiry=self.complete()
        outside=self.root/'keep.json';outside.write_bytes(self.path.read_bytes())
        self.path.unlink();self.path.symlink_to(outside)
        self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['skipped'],1)
        self.assertTrue(outside.exists())
        alias=self.root/'linked-matches';alias.symlink_to(self.results,target_is_directory=True)
        with self.assertRaises(ValueError): log_retention.prune(self.db,alias,apply=True,now=expiry)

    def test_runner_automatically_prunes_at_startup(self):
        expiry=self.complete()
        with patch('ottv2.log_retention.time.time',return_value=expiry):
            events=[]
            local_runner.run_loop(self.db,self.results,max_cycles=1,emit=events.append)
        self.assertFalse(self.path.exists())
        self.assertEqual(events[0]['state'],'logs_pruned')

    def test_cleanup_error_does_not_stop_match_processing(self):
        with patch('ottv2.log_retention.prune',side_effect=PermissionError('not now')):
            events=[]
            answer=local_runner.run_loop(self.db,self.results,max_cycles=1,runner=self.fake_runner,emit=events.append)
        self.assertEqual(answer['completed'],1)
        self.assertEqual(events[0]['state'],'log_cleanup_retry')

    def test_cleanup_runs_once_per_minute_instead_of_every_cycle(self):
        match_queue.cancel_job(self.db,self.match_id)
        with patch('ottv2.log_retention.prune',return_value={'deleted':0,'skipped':0}) as prune, \
             patch('threading.Event.wait',return_value=False), \
             patch('ottv2.local_runner.time.monotonic',return_value=1000):
            local_runner.run_loop(self.db,self.results,max_cycles=4)
        self.assertEqual(prune.call_count,1)

    def test_bounded_batches_continue_after_restart(self):
        expiry=self.complete()
        with match_queue._write(self.db) as db:
            for number in range(1,6):
                match_id=f'{number:032x}'
                payload={**self.job,'match_id':match_id}
                receipt={**match_queue.get_job(self.db,self.match_id)['elo_receipt'],'match_id':match_id}
                db.execute("INSERT INTO jobs(match_id,state,payload,result_path,receipt_json) VALUES (?,'completed',?,?,?)",
                           (match_id,json.dumps(payload),str(self.results/(match_id+'.json')),json.dumps(receipt)))
                log_retention.record_completion(db,match_id,{'winner':'A','reason':'goal','valid_move_count':1},
                                                now=self.saved['completed_at'])
        with patch.object(log_retention,'BATCH_SIZE',3):
            self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry)['deleted'],3)
            self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry+1)['deleted'],3)
            self.assertEqual(log_retention.prune(self.db,self.results,apply=True,now=expiry+2)['checked'],0)
