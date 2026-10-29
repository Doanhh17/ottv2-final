from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import copy
import http.client
import json
from pathlib import Path
from http.client import HTTPConnection
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import MagicMock, patch

from ottv2 import accounts, backup, cluster, daily_quota, elo_store, local_arena, local_worker, match_queue, portal_api, remote_worker
from tests.test_local_worker import WorkerFixture


class ClusterTests(WorkerFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        cluster.enable(self.db)
        self.coordinator=cluster.Coordinator(self.db,self.results)
        self.keys={}
        for worker in ('worker01','worker02'):
            path=self.root/(worker+'.json')
            cluster.register(self.db,worker,'http://127.0.0.1:8765',path)
            self.keys[worker]=json.loads(path.read_text())['token']

    def claim(self,worker='worker01',now=1000):
        return self.coordinator.call(worker,{'action':'claim'},now=now)

    def test_old_worker_leaves_new_job_pending_without_attempt_or_quota_loss(self):
        before=match_queue.get_job(self.db,self.match_id)
        for version in (1,2,3,4):
            answer=self.coordinator.call('worker01',{'action':'claim'},now=1000,rules_version=version)
            self.assertEqual(answer,{'state':'idle','reason':'worker_update_required'})
        after=match_queue.get_job(self.db,self.match_id)
        self.assertEqual((after['state'],after['attempts']),('pending',0))
        self.assertEqual(after['players'],before['players'])
        self.assertIn('2,5',cluster.status(self.db,now=1000)['workers'][0]['last_error'])
        assignment=self.claim(now=1001)
        self.assertEqual(assignment['job']['limits'],{'cpu_budget_seconds':7.0,'response_limit_seconds':2.5,'memory_limit_mib_per_bot':64,'max_valid_moves':50,'move_limit_tiebreak':'piece_count'})
        self.assertEqual(cluster.status(self.db,now=1001)['workers'][0]['last_error'],'')

    def test_pinned_legacy_job_and_delayed_result_remain_scoreable(self):
        from ottv2.rules import LEGACY_LIMITS
        with match_queue._write(self.db) as db:
            job=json.loads(db.execute('SELECT payload FROM jobs WHERE match_id=?',(self.match_id,)).fetchone()[0])
            job['limits']=dict(LEGACY_LIMITS)
            db.execute('UPDATE jobs SET payload=? WHERE match_id=?',(json.dumps(job),self.match_id))
        assignment=self.coordinator.call('worker01',{'action':'claim'},now=1000,rules_version=1)
        self.assertEqual(assignment['job']['limits'],LEGACY_LIMITS)
        request=self.request(assignment)
        self.assertTrue(self.coordinator.call('worker01',request,now=1001)['elo_applied'])
        self.assertFalse(self.coordinator.call('worker01',request,now=1002)['elo_applied'])

    def record(self,assignment):
        with TemporaryDirectory() as temp:
            path=Path(temp)/(assignment['job']['match_id']+'.json')
            self.fake_runner(assignment['job'],path)
            return json.loads(path.read_text())

    def request(self,assignment,action='complete'):
        body={'action':action,'match_id':assignment['job']['match_id'],'lease_token':assignment['lease_token']}
        if action=='complete': body['record']=self.record(assignment)
        if action=='fail': body['message']='controlled failure'
        return body

    def test_registry_does_not_store_tokens_and_revoke_rejects_auth(self):
        token=self.keys['worker01']
        self.assertEqual(self.coordinator.authenticate(token),'worker01')
        self.assertNotIn(token.encode(),self.db.read_bytes())
        with self.assertRaises(PermissionError):self.coordinator.authenticate('0'*64)
        self.claim()
        cluster.revoke(self.db,'worker01')
        with self.assertRaises(PermissionError):self.coordinator.authenticate(token)
        second=self.claim('worker02',1001)
        self.assertEqual(second['state'],'assigned')

    def test_two_workers_claim_different_jobs_concurrently_and_no_second_claim_when_busy(self):
        source=(Path(__file__).resolve().parent.parent/'examples/greedy/main.py').read_bytes()
        for identity in ('student03','student04'):self.import_source(identity,source)
        match_queue.find_match(self.db,'student03');match_queue.find_match(self.db,'student04')
        with ThreadPoolExecutor(2) as pool:
            assignments=list(pool.map(self.claim,('worker01','worker02')))
        self.assertEqual({a['state'] for a in assignments},{'assigned'})
        self.assertEqual(len({a['job']['match_id'] for a in assignments}),2)
        self.assertNotIn('bot_store',assignments[0]['job'])
        self.assertEqual(self.claim()['state'],'busy')
        for worker,a in zip(('worker01','worker02'),assignments):
            self.assertEqual(self.coordinator.call(worker,self.request(a),now=1001)['state'],'completed')
        self.assertEqual(len(elo_store.standings(self.elo)),4)

    def test_duplicate_result_after_lost_response_is_idempotent_even_after_lease_expiry(self):
        a=self.claim();request=self.request(a)
        self.assertTrue(self.coordinator.call('worker01',request,now=1001)['elo_applied'])
        self.assertFalse(self.coordinator.call('worker01',request,now=9999)['elo_applied'])
        self.assert_completed()
        self.assertEqual(daily_quota.status(self.db,'student01')['active']['used'],1)

    def test_expired_worker_cannot_upload_after_reassignment(self):
        old=self.claim();request=self.request(old)
        new=self.claim('worker02',1181)
        self.assertEqual(new['job']['match_id'],old['job']['match_id'])
        self.assertNotEqual(new['lease_token'],old['lease_token'])
        with self.assertRaises(cluster.LeaseRejected):self.coordinator.call('worker01',request,now=1182)
        self.assertFalse((self.results/(self.match_id+'.json')).exists())
        self.coordinator.call('worker02',self.request(new),now=1182)
        self.assertEqual(self.assert_completed()['attempts'],2)

    def test_heartbeat_extends_lease_but_not_hard_deadline(self):
        a=self.claim();body=self.request(a,'heartbeat')
        self.assertEqual(self.coordinator.call('worker01',body,now=1100)['expires_at'],1280)
        self.assertEqual(self.coordinator.call('worker01',body,now=1200)['expires_at'],1300)
        with self.assertRaises(cluster.LeaseRejected):self.coordinator.call('worker01',body,now=1300)

    def test_invalid_result_is_rejected_before_publication_or_rating(self):
        a=self.claim();valid=self.request(a)
        for mutation in ('final','winner','map'):
            bad=copy.deepcopy(valid)
            if mutation=='final':bad['record']['final_state']['board']={}
            if mutation=='winner':bad['record']['result']['winner']='A' if valid['record']['result']['winner']=='B' else 'B'
            if mutation=='map':bad['record']['map']['sha256']='f'*64
            with self.assertRaises(ValueError):self.coordinator.call('worker01',bad,now=1001)
            self.assertFalse((self.results/(self.match_id+'.json')).exists())
            self.assertEqual(elo_store.standings(self.elo),[])
        self.coordinator.call('worker01',valid,now=1002)
        self.assert_completed()

    def test_crash_between_elo_and_queue_commit_recovers_without_scoring_twice(self):
        a=self.claim();request=self.request(a)
        with patch('ottv2.local_worker._finish',side_effect=RuntimeError('crash after Elo')):
            with self.assertRaises(RuntimeError):self.coordinator.call('worker01',request,now=1001)
        self.assertEqual(match_queue.get_job(self.db,self.match_id)['state'],'running')
        self.assertEqual(elo_store.standings(self.elo)[0]['played'],1)
        self.coordinator.recover(now=1002)
        self.assert_completed()
        self.assertFalse(self.coordinator.call('worker01',request,now=1003)['elo_applied'])

    def test_crash_after_queue_commit_does_not_leave_worker_busy(self):
        a=self.claim();request=self.request(a)
        finish=local_worker._finish
        def crash(*args):
            finish(*args)
            raise RuntimeError('crash before retiring lease')
        with patch('ottv2.local_worker._finish',side_effect=crash):
            with self.assertRaises(RuntimeError):self.coordinator.call('worker01',request,now=1001)
        self.assert_completed()
        self.assertEqual(self.claim(now=1002)['state'],'idle')
        self.assertFalse(any(row['match_id'] for row in cluster.status(self.db)['workers']))
        self.assertFalse(self.coordinator.call('worker01',request,now=1003)['elo_applied'])
        self.assertEqual(elo_store.standings(self.elo)[0]['played'],1)

    def test_log_cleanup_failure_does_not_stop_distributed_master(self):
        events=[]
        with patch('ottv2.cluster.master_lock',return_value=nullcontext()), \
                patch('ottv2.log_retention.prune',side_effect=OSError('disk unavailable')):
            answer=cluster.run_loop(self.coordinator,max_cycles=1,emit=events.append)
        self.assertEqual(answer['cycles'],1)
        self.assertTrue(any(event['state']=='log_cleanup_retry' for event in events))
        self.assertEqual(match_queue.get_job(self.db,self.match_id)['state'],'pending')

    def test_three_worker_failures_return_quota_without_rating(self):
        for number in range(3):
            a=self.claim(now=1000+number)
            answer=self.coordinator.call('worker01',self.request(a,'fail'),now=1000+number)
        self.assertEqual(answer['state'],'failed')
        self.assertEqual(match_queue.get_job(self.db,self.match_id)['attempts'],3)
        self.assertEqual(elo_store.standings(self.elo),[])
        self.assertEqual(daily_quota.status(self.db,'student01')['active']['remaining'],3)

    def test_three_lost_workers_are_terminal_and_stale_heartbeats_fail(self):
        a=None
        for index in range(3):
            a=self.claim(now=1000+index*181)
        self.coordinator.recover(now=1543)
        self.assertEqual(match_queue.get_job(self.db,self.match_id)['state'],'failed')
        with self.assertRaises(cluster.LeaseRejected):
            self.coordinator.call('worker01',self.request(a,'heartbeat'),now=1543)

    def test_switching_to_local_requires_no_running_jobs(self):
        a=self.claim()
        with self.assertRaises(ValueError):cluster.use_local(self.db)
        self.coordinator.call('worker01',self.request(a),now=1001)
        cluster.use_local(self.db)
        self.assertEqual(local_worker.run_once(self.db,self.results,self.fake_runner),{'state':'idle'})

    def test_url_and_key_file_validation(self):
        for url in ('http://example.com','https://u:p@example.com','https://example.com/a','https://example.com/?x=1'):
            with self.assertRaises(ValueError):cluster.master_url(url)
        self.assertEqual(cluster.master_url('https://arena.example.com/'),'https://arena.example.com')
        path=self.root/'worker01.json'
        self.assertEqual(remote_worker.load_config(path)['worker_id'],'worker01')
        with self.assertRaises(FileExistsError):cluster.register(self.db,'newworker','https://arena.example.com',path)

    def test_outbox_retains_on_network_failure_and_clears_only_on_ack_or_fence(self):
        a=self.claim();path=self.root/'outbox.json';remote_worker._save(path,self.request(a))
        class Client:
            def call(self,request):raise remote_worker.RPCError(503)
        with self.assertRaises(remote_worker.RPCError):remote_worker.flush(Client(),path)
        self.assertTrue(path.exists())
        class Stale:
            def call(self,request):raise remote_worker.RPCError(409)
        self.assertEqual(remote_worker.flush(Stale(),path)['state'],'lease_rejected')
        self.assertFalse(path.exists())

    def test_socket_and_truncated_response_errors_use_retry_path(self):
        cfg=remote_worker.load_config(self.root/'worker01.json')
        for stage in ('open','read'):
            for error in (TimeoutError('timeout'),ConnectionResetError('reset'),
                          http.client.IncompleteRead(b'partial')):
                with self.subTest(stage=stage,error=type(error).__name__):
                    opener=MagicMock()
                    if stage=='open':opener.open.side_effect=error
                    else:opener.open.return_value.__enter__.return_value.read.side_effect=error
                    with patch('urllib.request.build_opener',return_value=opener):
                        with self.assertRaises(remote_worker.RPCError) as caught:
                            remote_worker.Client(cfg).call({'action':'claim'})
                    self.assertEqual(caught.exception.status,503)

    def test_embedded_worker_retries_timeout_and_remains_running(self):
        stopped=Event();events=[];opener=MagicMock()
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"state":"idle"}'
        opener.open.side_effect=[TimeoutError('temporary timeout'),response]
        def emit(event):
            events.append(event)
            if event['state']=='idle':stopped.set()
        with patch('urllib.request.build_opener',return_value=opener), \
                patch('ottv2.local_worker._worker_lock',return_value=nullcontext()):
            answer=remote_worker.run_loop(self.root/'worker01.json',self.root/'worker-retry',
                stop_event=stopped,runner=self.fake_runner,interval=0.1,emit=emit)
        self.assertEqual(answer['jobs'],0)
        self.assertEqual([e['state'] for e in events],['connection_retry','idle'])

    def test_worker_executes_authenticated_sources_and_rejects_hash_changes(self):
        a=self.claim();root=self.root/'remote';root.mkdir()
        class Client:
            cfg={'worker_id':'worker01'}
        self.assertEqual(remote_worker.execute(Client(),a,root,runner=self.fake_runner)['state'],'result_pending')
        body=json.loads((root/'outbox.json').read_text())
        self.coordinator.call('worker01',body,now=1001)
        self.assert_completed()

    def test_http_worker_pipeline_lost_ack_retries_outbox_without_duplicate_score(self):
        account_db=self.root/'accounts.sqlite3';accounts.initialize(account_db,self.db)
        server=portal_api.create_server(self.store,0,self.results,self.db,account_db)
        server.coordinator=self.coordinator
        thread=Thread(target=lambda:server.serve_forever(poll_interval=0.01),daemon=True);thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda:thread.join(2))
        self.addCleanup(server.shutdown)
        cfg={'worker_id':'worker01','url':f'http://127.0.0.1:{server.server_port}','token':self.keys['worker01']}
        client=remote_worker.Client(cfg)
        bad=remote_worker.Client({**cfg,'token':'f'*64})
        with self.assertRaises(remote_worker.RPCError) as caught:bad.call({'action':'claim'})
        self.assertEqual(caught.exception.status,401)
        # Simulate the old HTTP client: valid key, no rules-version header.
        from urllib.request import Request,urlopen
        old=Request(cfg['url']+'/api/worker',data=b'{"action":"claim"}',method='POST',
                    headers={'Authorization':'Bearer '+cfg['token'],'Content-Type':'application/json'})
        with urlopen(old,timeout=3) as response:
            self.assertEqual(json.load(response)['reason'],'worker_update_required')
        for version in ('1','2','3','4'):
            old_version=Request(cfg['url']+'/api/worker',data=b'{"action":"claim"}',method='POST',
                headers={'Authorization':'Bearer '+cfg['token'],'Content-Type':'application/json',
                         'X-OTTV2-Rules-Version':version})
            with urlopen(old_version,timeout=3) as response:
                self.assertEqual(json.load(response)['reason'],'worker_update_required')
        self.assertEqual(match_queue.get_job(self.db,self.match_id)['attempts'],0)
        a=client.call({'action':'claim'})
        root=self.root/'http-worker';root.mkdir()
        remote_worker.execute(client,a,root,runner=self.fake_runner)
        class LoseAck:
            def call(self,request):
                client.call(request)
                raise remote_worker.RPCError(503)
        outbox=root/'outbox.json'
        with self.assertRaises(remote_worker.RPCError):remote_worker.flush(LoseAck(),outbox)
        self.assertTrue(outbox.exists())
        self.assert_completed()
        self.assertFalse(remote_worker.flush(client,outbox)['elo_applied'])
        self.assertFalse(outbox.exists())
        self.assert_completed()
        self.assertNotIn('token',json.dumps(cluster.status(self.db)))

    def test_restore_expires_active_leases_and_keeps_worker_registry(self):
        self.claim()
        archive=self.root.parent/(self.root.name+'-cluster-backup.tar.gz')
        self.addCleanup(lambda:archive.unlink(missing_ok=True))
        backup.create(self.root,archive)
        restored=self.root/'restored'
        backup.restore(archive,restored)
        coordinator=cluster.Coordinator(restored/'queue.sqlite3',restored/'matches')
        self.assertEqual(coordinator.authenticate(self.keys['worker01']),'worker01')
        new=coordinator.call('worker02',{'action':'claim'},now=1001)
        self.assertEqual(new['state'],'assigned')
        self.assertEqual(new['job']['match_id'],self.match_id)

    def test_worker_rejects_source_tampering_without_executing_bot(self):
        a=self.claim();a['sources']['A']='YQ=='
        root=self.root/'bad-source';root.mkdir()
        class Client:cfg={'worker_id':'worker01'}
        with patch('ottv2.local_worker._run_job') as runner:
            self.assertEqual(remote_worker.execute(Client(),a,root)['state'],'worker_error')
            runner.assert_not_called()
        self.assertEqual(json.loads((root/'outbox.json').read_text())['action'],'fail')


@unittest.skipUnless(sys.platform.startswith('linux'),'requires Linux Isolate')
class DistributedIsolateTests(WorkerFixture,unittest.TestCase):
    real=True

    def test_two_authenticated_worker_clients_run_real_isolate_matches_via_http(self):
        account_db=self.root/'accounts.sqlite3';accounts.initialize(account_db,self.db)
        cluster.enable(self.db)
        server=portal_api.create_server(self.store,0,self.results,self.db,account_db)
        server.coordinator=cluster.Coordinator(self.db,self.results)
        thread=Thread(target=lambda:server.serve_forever(poll_interval=0.01),daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(lambda:thread.join(3));self.addCleanup(server.shutdown)
        for index in range(2):
            if index:
                match_queue.find_match(self.db,'student01');match_queue.find_match(self.db,'student02')
            else:
                from ottv2.rules import LEGACY_LIMITS
                with match_queue._write(self.db) as db:
                    row=db.execute("SELECT match_id,payload FROM jobs WHERE state='pending'").fetchone()
                    job=json.loads(row['payload']);job['limits']=dict(LEGACY_LIMITS)
                    db.execute('UPDATE jobs SET payload=? WHERE match_id=?',(json.dumps(job),row['match_id']))
            identity='worker0'+str(index+1);path=self.root/(identity+'.json')
            cluster.register(self.db,identity,f'http://127.0.0.1:{server.server_port}',path)
            client=remote_worker.Client(remote_worker.load_config(path))
            assignment=client.call({'action':'claim'})
            directory=self.root/identity;directory.mkdir()
            with local_worker._worker_lock():
                self.assertEqual(remote_worker.execute(client,assignment,directory)['state'],'result_pending')
            self.assertEqual(remote_worker.flush(client,directory/'outbox.json')['state'],'completed')
            self.assertTrue(all(row['played']==index+1 for row in elo_store.standings(self.elo)))
        self.assertEqual(len(cluster.status(self.db)['workers']),2)

    def test_master_embedded_worker_finishes_then_stops_cleanly(self):
        account_db=self.root/'accounts.sqlite3';accounts.initialize(account_db,self.db)
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
        credential=self.root/'worker.json'
        cluster.register(self.db,'worker01',f'http://127.0.0.1:{port}',credential)
        stopped=Event();events=[]
        def emit(value):
            events.append(value)
            if value.get('state')=='completed':stopped.set()
        result=local_arena.run_arena(self.db,self.results,account_db=account_db,port=port,distributed=True,
                    worker_config=credential,worker_directory=self.root/'worker',stop_event=stopped,
                    interval=0.1,max_cycles=200,emit=emit)
        self.assertEqual(result['mode'],'distributed')
        self.assertEqual(match_queue.get_job(self.db,self.match_id)['state'],'completed')
        self.assertTrue(stopped.is_set())
        self.assertTrue(any(e['state']=='assigned' for e in events))


if __name__=='__main__':unittest.main()
