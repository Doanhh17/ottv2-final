from contextlib import closing, nullcontext
import json
import os
from pathlib import Path
import sqlite3
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock, Thread
import unittest
from unittest.mock import patch

from ottv2 import accounts, cluster, elo_store, local_arena, local_worker, match_archive, match_queue, portal_api, remote_worker, worker_pool
from ottv2.isolated_session import ISOLATE, BOX_ROOT, validate_box_pair
from tests.test_local_worker import WorkerFixture


class PoolTests(WorkerFixture, unittest.TestCase):
    def test_extended_pool_migrates_layout_and_flushes_original_outbox(self):
        path=self.root/'pool.json'
        worker_pool.register(self.db,'lab01','http://localhost:8765',path,slots=8)
        old=worker_pool.load_config(path)
        folder=self.root/'pool';folder.mkdir()
        (folder/'pool-layout.json').write_text(json.dumps({'host_id':old['host_id'],'url':old['url'],
            'workers':[w['worker_id'] for w in old['workers']]}))
        outbox=folder/'lab01-08/outbox.json';outbox.parent.mkdir();outbox.write_text('retained result')
        worker_pool.extend(self.db,path,12)
        with patch('ottv2.remote_worker.flush') as flush,patch('ottv2.remote_worker.run_client',return_value={'jobs':0}):
            result=worker_pool.run_loop(path,folder,slots=12,runner=lambda:None)
        self.assertEqual(result['slots'],12)
        self.assertEqual(len(json.loads((folder/'pool-layout.json').read_text())['workers']),12)
        flush.assert_called_once()
        self.assertEqual(flush.call_args.args[1],outbox)
        self.assertTrue(outbox.exists())

    def test_extend_adds_four_keys_preserving_every_old_slot_and_rejects_bad_registry(self):
        path=self.root/'pool.json'
        worker_pool.register(self.db,'worker-pc02','http://localhost:8765',path,slots=8)
        old=worker_pool.load_config(path)
        answer=worker_pool.extend(self.db,path,12)
        extended=worker_pool.load_config(path)
        self.assertEqual(extended['workers'][:8],old['workers'])
        self.assertEqual(len(extended['workers']),12)
        self.assertEqual(answer['slots'],12)
        coordinator=cluster.Coordinator(self.db,self.results)
        for w in extended['workers']:
            self.assertEqual(coordinator.authenticate(w['token']),w['worker_id'])
        worker_pool.extend(self.db,path,12)
        self.assertEqual(worker_pool.load_config(path),extended)
        with self.assertRaises(ValueError):worker_pool.extend(self.db,path,6)
        wrong={**extended,'workers':[{**w,'token':'f'*64} for w in extended['workers']]}
        # A different registered secret is never silently replaced.
        wrong['workers'][1]['token']='e'*64
        path.write_text(json.dumps(wrong));path.chmod(0o600)
        with self.assertRaises(ValueError):worker_pool.extend(self.db,path,12)

    def test_extension_write_failure_rolls_back_new_registry_entries(self):
        path=self.root/'pool.json'
        worker_pool.register(self.db,'worker-pc04','http://localhost:8765',path,slots=8)
        old=path.read_bytes()
        with patch('ottv2.remote_worker._save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):worker_pool.extend(self.db,path,12)
        self.assertEqual(path.read_bytes(),old)
        self.assertEqual(len(cluster.status(self.db)['workers']),8)

    def test_named_profile_caps_slots_but_flushes_disabled_outboxes(self):
        path = self.root/'pool.json'
        worker_pool.register(self.db,'worker-pc02','http://localhost:8765',path)
        folder = self.root/'pool'
        retained = folder/'worker-pc02-08'/'outbox.json'
        retained.parent.mkdir(parents=True); retained.write_text('retained')
        seen = []
        def client(cfg,*args,**kwargs):
            seen.append(cfg['worker_id'])
            return {'jobs':0}
        with patch.object(worker_pool,'PROFILE_PATH',self.root/'profile.json'):
            worker_pool.configure_profile(path,'worker_sinh',4)
            with self.assertRaises(ValueError): worker_pool.load_profile('other-host')
            with patch('ottv2.remote_worker.run_client',side_effect=client),patch('ottv2.remote_worker.flush') as flush:
                answer = worker_pool.run_loop(path,folder,slots=8,runner=lambda:None)
                flush.assert_called_once()
        self.assertEqual(answer['slots'],4)
        self.assertEqual(len(seen),4)
        self.assertTrue(retained.exists())
        before = [(r['worker_id'],r['enabled']) for r in cluster.status(self.db)['workers']]
        worker_pool.set_label(self.db,'worker-pc02','worker_sinh')
        after = cluster.status(self.db)['workers']
        self.assertEqual(before,[(r['worker_id'],r['enabled']) for r in after])
        self.assertEqual(after[0]['display_name'],'worker_sinh / 01')

    def test_master_ceiling_stops_new_assignments_but_retains_existing_lease(self):
        path=self.root/'pool.json'
        worker_pool.register(self.db,'worker-pc02','http://localhost:8765',path,slots=2)
        coordinator=cluster.Coordinator(self.db,self.results)
        assignment=coordinator.call('worker-pc02-02',{'action':'claim'})
        self.assertEqual(assignment['state'],'assigned')
        worker_pool.set_label(self.db,'worker-pc02','worker_sinh',1)
        self.assertEqual(coordinator.call('worker-pc02-02',{'action':'claim'})['state'],'busy')
        coordinator.call('worker-pc02-02',{'action':'heartbeat','match_id':assignment['job']['match_id'],'lease_token':assignment['lease_token']})
        with match_queue._write(self.db) as db:
            db.execute("UPDATE cluster_leases SET state='expired'")
        answer=coordinator.call('worker-pc02-02',{'action':'claim'})
        self.assertEqual(answer,{'state':'idle','reason':'slot_limit'})
        workers=cluster.status(self.db)['workers']
        self.assertTrue(workers[0]['slot_enabled'])
        self.assertFalse(workers[1]['slot_enabled'])

    def setUp(self):
        super().setUp()
        profile = patch.object(worker_pool,'PROFILE_PATH',self.root/'test-profile.json')
        profile.start()
        self.addCleanup(profile.stop)
        loose_lock = patch('ottv2.local_worker._worker_lock', side_effect=lambda *a,**k:nullcontext())
        loose_lock.start()
        self.addCleanup(loose_lock.stop)
        cluster.enable(self.db)

    def serve(self):
        db = self.root/'accounts.sqlite3'
        accounts.initialize(db,self.db)
        server = portal_api.create_server(self.store,0,self.results,self.db,db)
        server.coordinator = cluster.Coordinator(self.db,self.results)
        thread = Thread(target=server.serve_forever,kwargs={'poll_interval':0.01})
        thread.start()
        def cleanup():
            server.shutdown();thread.join(5);server.server_close()
        self.addCleanup(cleanup)
        return f'http://127.0.0.1:{server.server_port}'

    def test_registration_has_independent_private_keys_and_no_plaintext_registry(self):
        path = self.root/'pool.json'
        worker_pool.register(self.db,'lab01','http://localhost:8765',path)
        cfg = worker_pool.load_config(path)
        self.assertEqual(len(cfg['workers']),8)
        self.assertEqual(len({w['token'] for w in cfg['workers']}),8)
        self.assertEqual(path.stat().st_mode & 0o077,0)
        for worker in cfg['workers']:
            self.assertNotIn(worker['token'].encode(),self.db.read_bytes())
        with self.assertRaises(FileExistsError):
            worker_pool.register(self.db,'different','http://localhost:8765',path)
        with self.assertRaises(sqlite3.IntegrityError):
            worker_pool.register(self.db,'lab01','http://localhost:8765',self.root/'new.json')
        self.assertFalse((self.root/'new.json').exists())
        self.assertEqual(len(cluster.status(self.db)['workers']),8)

    def test_config_refuses_oversize_duplicate_keys_tokens_and_wrong_ids(self):
        path = self.root/'pool.json'
        worker_pool.register(self.db,'lab01','http://localhost:8765',path,slots=2)
        original = path.read_text()
        cfg=json.loads(original)
        variants=[{**cfg,'workers':cfg['workers']*13}, {**cfg,'version':True},
                  {**cfg,'workers':[cfg['workers'][0],{**cfg['workers'][1],'token':cfg['workers'][0]['token']}]},
                  {**cfg,'workers':[{**cfg['workers'][0],'worker_id':'someone-01'}]}]
        for value in variants:
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):worker_pool.load_config(path)
        path.write_text('{"version":1,"version":1}')
        with self.assertRaises(ValueError):worker_pool.load_config(path)
        path.write_text(original);path.chmod(0o644)
        with self.assertRaises(ValueError):worker_pool.load_config(path)

    def test_revocation_expires_all_slots_and_invalid_file_changes_none(self):
        path=self.root/'pool.json'
        worker_pool.register(self.db,'lab01','http://localhost:8765',path,slots=3)
        cfg=worker_pool.load_config(path)
        coordinator=cluster.Coordinator(self.db,self.results)
        coordinator.call(cfg['workers'][0]['worker_id'],{'action':'claim'},now=1000)
        wrong=json.loads(path.read_text());wrong['workers'][1]['token']='f'*64
        bad=self.root/'bad.json';bad.write_text(json.dumps(wrong));bad.chmod(0o600)
        with self.assertRaises(ValueError):worker_pool.revoke(self.db,bad)
        self.assertTrue(all(w['enabled'] for w in cluster.status(self.db)['workers']))
        worker_pool.revoke(self.db,path)
        self.assertTrue(all(not w['enabled'] for w in cluster.status(self.db)['workers']))
        for worker in cfg['workers']:
            with self.assertRaises(PermissionError):coordinator.authenticate(worker['token'])
        with closing(elo_store._connect(self.db,readonly=True)) as db:
            self.assertEqual(db.execute('SELECT expires_at FROM cluster_leases').fetchone()[0],0)

    def test_8_slots_over_http_hold_distinct_jobs_and_lost_ack_never_double_scores(self):
        source=(Path(__file__).resolve().parent.parent/'examples/greedy/main.py').read_bytes()
        for i in range(3,17):self.import_source(f'student{i:02d}',source)
        for i in range(3,17,2):
            match_queue.find_match(self.db,f'student{i:02d}')
            match_queue.find_match(self.db,f'student{i+1:02d}')
        path=self.root/'pool.json'
        worker_pool.register(self.db,'lab01',self.serve(),path)
        barrier=Barrier(8);lock=Lock();seen=set();active=0;peak=0;dropped=False
        def runner(job,output):
            nonlocal active,peak
            with lock:
                self.assertNotIn(job['match_id'],seen)
                seen.add(job['match_id']);active+=1;peak=max(peak,active)
            barrier.wait(timeout=40)
            try:self.fake_runner(job,output)
            finally:
                with lock:active-=1
        original=remote_worker.Client.call
        def call(client,request):
            nonlocal dropped
            answer=original(client,request)
            if request['action']=='complete' and client.cfg['worker_id']=='lab01-01' and not dropped:
                dropped=True
                raise remote_worker.RPCError(503)
            return answer
        with patch.object(remote_worker.Client,'call',call):
            result=worker_pool.run_loop(path,self.root/'pool',runner=runner,interval=0.01,max_jobs_per_slot=1)
        self.assertEqual(result['jobs'],8)
        self.assertEqual(peak,8)
        self.assertEqual(len(seen),8)
        self.assertTrue(dropped)
        self.assertFalse(list((self.root/'pool').rglob('outbox.json')))
        ratings=elo_store.standings(self.elo,limit=4000)
        self.assertEqual(len(ratings),16)
        self.assertTrue(all(r['played']==1 for r in ratings))
        with closing(elo_store._connect(self.db,readonly=True)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE state='completed'").fetchone()[0],8)
        self.assertNotIn('token',(self.root/'pool/pool-layout.json').read_text())

    def test_stop_waits_for_running_slots_to_publish_results(self):
        source=(Path(__file__).resolve().parent.parent/'examples/greedy/main.py').read_bytes()
        for who in ('student03','student04'):self.import_source(who,source)
        match_queue.find_match(self.db,'student03');match_queue.find_match(self.db,'student04')
        path=self.root/'pool.json';worker_pool.register(self.db,'lab01',self.serve(),path,slots=2)
        barrier=Barrier(3);release=Event();stop=Event();result=[];errors=[]
        def runner(job,output):
            barrier.wait(timeout=20);release.wait(20);self.fake_runner(job,output)
        def run():
            try:result.append(worker_pool.run_loop(path,self.root/'pool',runner=runner,stop_event=stop,interval=0.01))
            except BaseException as error:errors.append(error)
        thread=Thread(target=run);thread.start()
        try:
            barrier.wait(timeout=20);stop.set();self.assertTrue(thread.is_alive());release.set();thread.join(20)
        finally:
            stop.set();release.set();thread.join(20)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(result[0]['jobs'],2)
        self.assertTrue(all(r['played']==1 for r in elo_store.standings(self.elo)))

    def test_resource_failure_happens_before_claim_and_never_rewrites_layout(self):
        path=self.root/'pool.json';worker_pool.register(self.db,'lab01','http://localhost:8765',path,slots=2)
        with patch.object(worker_pool,'check_resources',side_effect=ValueError('CPU insufficient')), patch.object(remote_worker.Client,'call') as call:
            with self.assertRaisesRegex(ValueError,'CPU insufficient'):
                worker_pool.run_loop(path,self.root/'pool')
        call.assert_not_called()
        self.assertEqual(match_queue.get_job(self.db,self.match_id)['state'],'pending')
        self.assertFalse((self.root/'pool/pool-layout.json').exists())

    def test_failure_in_one_slot_stops_siblings_without_claiming_more_work(self):
        path=self.root/'pool.json';worker_pool.register(self.db,'lab01','http://localhost:8765',path,slots=3)
        stopped=Event()
        def run(cfg,directory,**kwargs):
            if cfg['worker_id'].endswith('-01'):raise ValueError('disabled')
            self.assertTrue(kwargs['stop_event'].wait(5));stopped.set()
            return {'jobs':0}
        with patch.object(remote_worker,'run_client',side_effect=run):
            with self.assertRaises(RuntimeError):worker_pool.run_loop(path,self.root/'pool',runner=self.fake_runner)
        self.assertTrue(stopped.is_set())

    def test_restart_flushes_disabled_slot_outbox_before_accepting_new_work(self):
        source=(Path(__file__).resolve().parent.parent/'examples/greedy/main.py').read_bytes()
        for who in ('student03','student04'):self.import_source(who,source)
        match_queue.find_match(self.db,'student03');match_queue.find_match(self.db,'student04')
        path=self.root/'pool.json';worker_pool.register(self.db,'lab01',self.serve(),path,slots=2)
        cfg=worker_pool.load_config(path)
        client=remote_worker.Client({**cfg['workers'][1],'url':cfg['url']})
        assignment=client.call({'action':'claim'})
        directory=self.root/'pool';folder=directory/'lab01-02';folder.mkdir(parents=True)
        remote_worker.execute(client,assignment,folder,runner=self.fake_runner,box_ids=(4,5))
        self.assertTrue((folder/'outbox.json').exists())
        result=worker_pool.run_loop(path,directory,slots=1,runner=self.fake_runner,interval=0.01,max_jobs_per_slot=1)
        self.assertEqual(result['jobs'],1)
        self.assertFalse((folder/'outbox.json').exists())
        self.assertTrue(all(r['played']==1 for r in elo_store.standings(self.elo)))

    def test_mismatched_layout_and_linked_slot_are_rejected_without_erasing_state(self):
        path=self.root/'pool.json';worker_pool.register(self.db,'lab01','http://localhost:8765',path,slots=2)
        directory=self.root/'pool';directory.mkdir()
        marker=directory/'pool-layout.json';marker.write_text('{"host_id":"other"}')
        with self.assertRaises(ValueError):worker_pool.run_loop(path,directory,runner=self.fake_runner)
        self.assertEqual(marker.read_text(),'{"host_id":"other"}')
        marker.unlink();old=directory/'outbox.json';old.write_text('{}')
        with self.assertRaises(ValueError):worker_pool.run_loop(path,directory,runner=self.fake_runner)
        self.assertEqual(old.read_text(),'{}');old.unlink()
        outside=self.root/'outside';outside.mkdir()
        (directory/'lab01-01').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):worker_pool.run_loop(path,directory,runner=self.fake_runner)
        self.assertTrue(outside.is_dir())


class PoolResourceTests(unittest.TestCase):
    def test_shared_cpu_accepts_eight_slots_on_two_cores_but_dedicated_refuses(self):
        with patch.object(worker_pool,'_physical_cpus',return_value=[0,2]), patch.object(worker_pool,'_available_memory_mib',return_value=4096), patch.object(worker_pool,'_cgroup_headroom',return_value=(2,4096)):
            plan=worker_pool.resource_plan(8)
            self.assertEqual(plan['cpu_mode'],'shared')
            self.assertTrue(plan['cpu_shared'])
            self.assertEqual(plan['required_cpu_cores'],1)
            self.assertEqual(plan['required_available_memory_mib'],2048)
            self.assertEqual(set(plan['box_cpus']),set(range(18)))
            self.assertEqual(set(plan['box_cpus'].values()),{0,2})
            with self.assertRaises(ValueError):worker_pool.resource_plan(8,cpu_mode='dedicated')
            with self.assertRaises(ValueError):worker_pool.resource_plan(8,cpu_mode='unknown')
            for limits in ((0.5,4096),(2,2047)):
                with patch.object(worker_pool,'_cgroup_headroom',return_value=limits):
                    with self.assertRaises(ValueError):worker_pool.resource_plan(8)

    def test_shared_config_can_repeat_cpus_and_mode_is_checked_before_claim(self):
        plan={'slots':8,'box_cpus':{i:i%2 for i in range(18)}}
        with patch.object(worker_pool,'resource_plan',return_value=plan) as resource, patch.object(worker_pool,'checked'), patch.object(Path,'read_text',return_value='\n'.join(f'box{i}.cpus = {i%2}' for i in range(18))):
            self.assertEqual(worker_pool.check_resources(8,cpu_mode='shared'),plan)
            resource.assert_called_once_with(8,cpu_mode='shared')
        with patch.object(worker_pool,'resource_plan',return_value=plan), patch.object(worker_pool,'checked'), patch.object(Path,'read_text',return_value='box0.cpus = 9'):
            with self.assertRaisesRegex(ValueError,'CPU'):worker_pool.check_resources(8)

    def test_cgroup_cpu_quota_and_memory_limit_can_reduce_visible_host_capacity(self):
        with patch.object(worker_pool,'_physical_cpus',return_value=list(range(64))), patch.object(worker_pool,'_available_memory_mib',return_value=16384):
            for limits in ((4,16384),(64,2047)):
                with patch.object(worker_pool,'_cgroup_headroom',return_value=limits):
                    with self.assertRaises(ValueError):worker_pool.resource_plan(8,cpu_mode="dedicated")

    def test_box_pairs_are_unique_and_exclude_ready_boxes(self):
        pairs=[worker_pool.boxes(i) for i in range(12)]
        self.assertEqual(len({box for pair in pairs for box in pair}),24)
        self.assertEqual(pairs[0],(2,3));self.assertEqual(pairs[-1],(24,25))
        for bad in (0,13,True,1.0,-1):
            with self.assertRaises(ValueError):worker_pool.slot_count(bad)
        for pair in ((0,0),(1,2),(16,18),(True,1)):
            with self.assertRaises(ValueError):validate_box_pair(pair)

    def test_plan_requires_dedicated_cpu_and_memory_headroom_and_preserves_other_config(self):
        with patch.object(worker_pool,'_physical_cpus',return_value=list(range(18))), patch.object(worker_pool,'_available_memory_mib',return_value=8192):
            plan=worker_pool.resource_plan(8,cpu_mode="dedicated")
            self.assertEqual(plan['required_cpu_cores'],18)
            self.assertEqual(plan['required_available_memory_mib'],2048)
            with TemporaryDirectory() as tmp:
                root=Path(tmp);source=root/'original';target=root/'planned'
                text='cg_root = /sys/fs/cgroup/isolate\nbox0.cpus = 7\nbox99.cpus = 99\n'
                source.write_text(text)
                worker_pool.render_isolate_config(8,target,source=source,cpu_mode="dedicated")
                self.assertEqual(source.read_text(),text)
                self.assertIn('box99.cpus = 99',target.read_text())
                self.assertIn('box17.cpus = 17',target.read_text())
                self.assertEqual(target.read_text().count('box0.cpus'),1)
                with self.assertRaises(FileExistsError):worker_pool.render_isolate_config(8,target,source=source,cpu_mode="dedicated")
        with patch.object(worker_pool,'_physical_cpus',return_value=list(range(16))), patch.object(worker_pool,'_available_memory_mib',return_value=8192):
            with self.assertRaises(ValueError):worker_pool.resource_plan(8,cpu_mode="dedicated")
        with patch.object(worker_pool,'_physical_cpus',return_value=list(range(18))), patch.object(worker_pool,'_available_memory_mib',return_value=2047):
            with self.assertRaises(ValueError):worker_pool.resource_plan(8,cpu_mode="dedicated")

    def test_host_gate_excludes_legacy_runner_but_leaves_ready_pair_available(self):
        with local_worker._host_gate(pool=True):
            with self.assertRaises(RuntimeError):
                with local_worker._host_gate():pass
            with local_worker._worker_lock():pass
            with self.assertRaises(RuntimeError):
                with local_worker._host_gate(pool=True):pass
        with local_worker._worker_lock((2,3)):
            with local_worker._worker_lock((4,5)):pass
            with self.assertRaises(RuntimeError):
                with local_worker._worker_lock((2,3)):pass


@unittest.skipUnless(sys.platform.startswith('linux') and Path(ISOLATE).exists(),'requires Ubuntu Isolate')
class PoolIsolateTests(WorkerFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        profile = patch.object(worker_pool,'PROFILE_PATH',self.root/'test-profile.json')
        profile.start()
        self.addCleanup(profile.stop)

    real=True

    def test_master_embedded_pool_finishes_real_match_and_stops_cleanly(self):
        db=self.root/'accounts.sqlite3';accounts.initialize(db,self.db)
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
        credential=self.root/'pool.json'
        worker_pool.register(self.db,'embedded',f'http://127.0.0.1:{port}',credential,slots=1)
        stopped=Event()
        def emit(record):
            if record.get('state')=='completed':stopped.set()
        # Only a light real smoke test on the small development PC; the public
        # CLI keeps its CPU/RAM preflight and cannot bypass it.
        with patch.object(worker_pool,'check_resources',return_value={'slots':1}):
            result=local_arena.run_arena(self.db,self.results,account_db=db,port=port,distributed=True,
                worker_pool_config=credential,worker_directory=self.root/'pool',worker_slots=1,
                stop_event=stopped,interval=0.1,max_cycles=200,emit=emit)
        self.assertTrue(stopped.is_set())
        self.assertEqual(result['mode'],'distributed')
        self.assert_completed()
        self.assertFalse(any((BOX_ROOT/str(i)).exists() for i in (2,3)))

    def test_twelve_real_matches_use_twenty_four_separate_boxes_and_clean_up(self):
        source=(Path(__file__).resolve().parent.parent/'examples/greedy/main.py').read_bytes()
        for i in range(3,25):self.import_source(f'student{i:02d}',source)
        for i in range(3,25,2):
            match_queue.find_match(self.db,f'student{i:02d}')
            match_queue.find_match(self.db,f'student{i+1:02d}')
        cluster.enable(self.db)
        url=PoolTests.serve(self)
        path=self.root/'pool.json';worker_pool.register(self.db,'real',url,path,slots=12)
        barrier=Barrier(12);mapping={};lock=Lock()
        def emit(record):
            if record['state']=='assigned':
                with lock:mapping[record['match_id']]=worker_pool.boxes(record['slot']-1)
        def runner(job,output):
            barrier.wait(timeout=40)
            local_worker._run_job(job,output,box_ids=mapping[job['match_id']])
        result=worker_pool.run_loop(path,self.root/'pool',runner=runner,emit=emit,interval=0.01,max_jobs_per_slot=1)
        self.assertEqual(result['jobs'],12)
        self.assertEqual(set(mapping.values()),{worker_pool.boxes(i) for i in range(12)})
        self.assertTrue(all(r['played']==1 for r in elo_store.standings(self.elo)))
        for match_id in mapping:
            _, record=match_archive.read_match(self.results,match_id)
            self.assertIn(record['result']['reason'],('goal','missing_kind','threefold_repetition'))
        self.assertFalse(any((BOX_ROOT/str(box)).exists() for box in range(2,26)))


if __name__=='__main__':unittest.main()
