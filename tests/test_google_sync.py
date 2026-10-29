from pathlib import Path
from datetime import datetime, timezone
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ottv2 import accounts, google_sync, match_queue, portal_data, queue_admin


class GoogleRegistrationTests(unittest.TestCase):
    def test_sync_loop_waits_one_minute_after_success_or_failure_and_can_stop(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                stop = Mock()
                stop.is_set.side_effect = [False, True]
                emit = Mock()
                bridge = object()
                with patch.object(google_sync, 'load_client', return_value=bridge), patch.object(
                    google_sync, 'sync_once', side_effect=google_sync.BridgeError('network') if failed else None,
                    return_value={'state': 'google_synced'}) as sync:
                    google_sync.run_loop(self.db, None, self.root/'client.json', stop, emit=emit)
                sync.assert_called_once_with(self.db, None, bridge)
                stop.wait.assert_called_once_with(60)
                self.assertEqual(emit.call_args.args[0]['state'], 'google_retry' if failed else 'google_synced')
                if failed:
                    self.assertIn('60 giây', google_sync.status(self.db)['last_error'])

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.queue = self.root/'queue.sqlite3'
        self.store=self.root/'bots';self.store.mkdir()
        match_queue.initialize(self.queue,self.store,Path(__file__).resolve().parent.parent/'maps/nine-piece.json')
        self.db = self.root/'accounts.sqlite3'
        accounts.initialize(self.db, self.queue)
        google_sync.initialize(self.db)
        rounds = patch.object(accounts, 'ROUNDS', 1000)
        rounds.start()
        self.addCleanup(rounds.stop)
        self.row = dict(response_id='google-response-1', email='student@vnu.edu.vn',
                        identity='230001', name='Sinh viên thử', repository='https://github.com/student/bot',
                        password='private-tournament-password')

    def test_account_email_and_receipt_commit_together_without_plaintext(self):
        receipt = google_sync.apply_registration(self.db, self.row)
        self.assertEqual(receipt['status'], 'accepted')
        _, user = accounts.login(self.db, '230001', self.row['password'])
        self.assertEqual(user['repository'], self.row['repository'])
        self.assertNotIn('email', user)
        self.assertNotIn(self.row['password'].encode(), self.db.read_bytes())
        self.assertEqual(len(google_sync.pending_acks(self.db)), 1)

    def test_replay_never_changes_password_or_repository(self):
        first = google_sync.apply_registration(self.db, self.row)
        retry = {**self.row, 'password':'attacker-password', 'repository':'https://github.com/other/bot'}
        self.assertEqual(first, google_sync.apply_registration(self.db, retry))
        self.assertTrue(accounts.login(self.db, '230001', self.row['password']))
        with self.assertRaises(ValueError):
            accounts.login(self.db, '230001', 'attacker-password')

    def test_rejected_password_can_be_resubmitted_with_same_email_and_student_id(self):
        rejected = {**self.row, 'password': 'x' * 11}
        self.assertEqual(google_sync.apply_registration(self.db, rejected)['status'], 'rejected')
        corrected = {**self.row, 'response_id': 'corrected-submission', 'password': 'x' * 12}
        self.assertEqual(google_sync.apply_registration(self.db, corrected)['status'], 'accepted')
        self.assertTrue(accounts.login(self.db, self.row['identity'], corrected['password']))
        upper = {**self.row, 'identity': '230002', 'email': 'second@vnu.edu.vn',
                 'response_id': 'too-long', 'password': 'y' * 257}
        self.assertEqual(google_sync.apply_registration(self.db, upper)['status'], 'rejected')
        upper.update(response_id='correct-upper-bound', password='y' * 256)
        self.assertEqual(google_sync.apply_registration(self.db, upper)['status'], 'accepted')

    def test_duplicate_email_and_identity_cannot_take_over(self):
        google_sync.apply_registration(self.db, self.row)
        for replacement in ({'identity':'230002'}, {'email':'other@vnu.edu.vn'}):
            answer = google_sync.apply_registration(self.db, {**self.row, **replacement, 'response_id':str(replacement)})
            self.assertEqual(answer['status'], 'rejected')
        self.assertTrue(accounts.login(self.db, '230001', self.row['password']))

    def test_reject_wrong_domain_bad_repo_and_invalid_password(self):
        variants = [{'email':'student@gmail.com'}, {'email':'s@vnu.edu.vn.evil.test'},
                    {'email':'s@sub.vnu.edu.vn'}, {'repository':'http://127.0.0.1/secret'},
                    {'password':'short'}, {'name':'x\n=IMPORTXML("...")'}, {'identity':'../student'}]
        for i, replacement in enumerate(variants):
            row = {**self.row, **replacement, 'response_id':str(i)}
            self.assertEqual(google_sync.apply_registration(self.db, row)['status'], 'rejected')
        with accounts.database(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 0)

    def test_manual_account_is_preserved_and_casefolded_email_is_unique(self):
        accounts.create_user(self.db, '230001', 'local-password-123')
        self.assertEqual(google_sync.apply_registration(self.db, self.row)['status'], 'rejected')
        self.row.update(identity='230002', response_id='second', email='STUDENT@VNU.EDU.VN')
        self.assertEqual(google_sync.apply_registration(self.db, self.row)['status'], 'accepted')
        self.row.update(identity='230003', response_id='third', email='student@vnu.edu.vn')
        self.assertEqual(google_sync.apply_registration(self.db, self.row)['status'], 'rejected')

    def test_sync_retries_ack_after_network_loss_without_recreating_account(self):
        class FakeBridge:
            def __init__(self, row):
                self.row, self.calls, self.fail = row, [], True
            def call(self, action, **data):
                self.calls.append(action)
                if action=='pull':
                    return {'registrations':[self.row]}
                if action=='pull_reset':
                    return {'resets':[]}
                if action=='ack':
                    if self.fail:
                        self.fail=False
                        raise google_sync.BridgeError('network')
                    return {'acknowledged':[r['response_id'] for r in data['receipts']]}
                return {'ok':True}
        bridge = FakeBridge(self.row)
        with self.assertRaises(google_sync.BridgeError):
            google_sync.sync_once(self.db, None, bridge, now=1792781300)
        self.assertEqual(len(google_sync.pending_acks(self.db)),1)
        token, _ = accounts.login(self.db,'230001',self.row['password'])
        google_sync.sync_once(self.db, None, bridge, now=1792781301)
        self.assertEqual(google_sync.pending_acks(self.db),[])
        self.assertIsNotNone(accounts.session(self.db,token))
        google_sync.sync_once(self.db, None, bridge, now=1792781302)
        self.assertEqual(bridge.calls.count('ranking'),1)

    def test_export_includes_operator_created_students_but_no_admin_or_secrets(self):
        google_sync.apply_registration(self.db,self.row)
        accounts.create_user(self.db,'admin','admin-password-123',role='admin')
        accounts.create_user(self.db,'demo','demo-password-123')
        rows = google_sync.ranking_rows(self.db,None,now=1792781300)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0][0:4],[1,'230001','Sinh viên thử',1000])
        self.assertEqual(rows[1][0:4],[2,'demo','demo',1000])
        self.assertNotIn('admin',str(rows))
        self.assertNotIn('demo-password-123',str(rows))
        self.assertNotIn(self.row['email'],str(rows))
        self.assertNotIn(self.row['password'],str(rows))

    def test_three_named_samples_appear_without_marking_them_google_verified(self):
        for identity in ('student01','student02','student03'):
            accounts.create_user(self.db,identity,'sample-password-123',display_name=identity)
        accounts.create_user(self.db,'other-local','local-password-123')
        accounts.create_user(self.db,'admin','admin-password-123',role='admin')
        rows=google_sync.ranking_rows(self.db,None,now=1792781300)
        self.assertEqual([row[1] for row in rows],['other-local','student01','student02','student03'])
        self.assertEqual([row[3] for row in rows],[1000,1000,1000,1000])
        with accounts.database(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM verified_google_users').fetchone()[0],0)

    def test_password_form_cannot_unlock_account_and_ranking_is_preserved(self):
        google_sync.apply_registration(self.db, self.row)
        portal_data.set_student_locked(self.queue, self.db, self.row['identity'], True)
        rows = google_sync.ranking_rows(self.db, None)
        self.assertEqual([r[1] for r in rows], [self.row['identity']])
        response = dict(response_id='locked-reset', email=self.row['email'], identity=self.row['identity'],
                        password='new-password-123', created_at=datetime.fromtimestamp(time.time()+1, timezone.utc).isoformat())
        self.assertEqual(google_sync.apply_password_reset(self.db, response)['status'], 'accepted')
        with self.assertRaisesRegex(ValueError, 'khóa'):
            accounts.login(self.db, self.row['identity'], response['password'])
        portal_data.set_student_locked(self.queue, self.db, self.row['identity'], False)
        self.assertTrue(accounts.login(self.db, self.row['identity'], response['password']))

    def test_ranking_is_due_at_two_minutes_and_failed_export_remains_due(self):
        class Bridge:
            fail=False
            calls=[]
            def call(self,action,**data):
                if action=='ranking':
                    self.calls.append(data['generated_at'])
                    if self.fail:raise google_sync.BridgeError('network')
                return {'ok':True}
        bridge=Bridge();start=1792781300
        google_sync.sync_once(self.db,None,bridge,now=start,sync_forms=False)
        google_sync.sync_once(self.db,None,bridge,now=start+119,sync_forms=False)
        self.assertEqual(bridge.calls,[start])
        google_sync.sync_once(self.db,None,bridge,now=start+120,sync_forms=False)
        bridge.fail=True
        with self.assertRaises(google_sync.BridgeError):
            google_sync.sync_once(self.db,None,bridge,now=start+240,sync_forms=False)
        self.assertEqual(google_sync._state(self.db,'ranking_exported_at'),str(start+120))
        bridge.fail=False
        google_sync.sync_once(self.db,None,bridge,now=start+241,sync_forms=False)
        self.assertEqual(google_sync._state(self.db,'ranking_exported_at'),str(start+241))

    def test_loop_keeps_one_minute_forms_and_two_minute_ranking_schedules(self):
        class Clock:
            elapsed=0
            waits=[]
            def is_set(self):return len(self.waits)>=4
            def wait(self,seconds):self.waits.append(seconds);self.elapsed+=seconds
        clock=Clock();calls=[]
        class Bridge:
            def call(self,action,**data):
                calls.append((action,clock.elapsed))
                return {'registrations':[]} if action=='pull' else {'resets':[]} if action=='pull_reset' else {'ok':True}
        with patch.object(google_sync,'load_client',return_value=Bridge()), patch.object(
                google_sync.time,'time',side_effect=lambda:1792781300+clock.elapsed), patch.object(
                google_sync.time,'monotonic',side_effect=lambda:clock.elapsed):
            google_sync.run_loop(self.db,None,self.root/'client.json',clock)
        self.assertEqual([t for action,t in calls if action=='pull'],[0,60,120,180])
        self.assertEqual([t for action,t in calls if action=='ranking'],[0,120])
        self.assertEqual(clock.waits,[60,60,60,60])

    def test_admin_status_has_no_google_secret_or_private_registration(self):
        google_sync._state(self.db,'last_success','2026-10-24T00:00:00+00:00')
        state=google_sync.status(self.db)
        self.assertEqual(set(state),{'enabled','last_success','last_error'})
        self.assertEqual(state['last_success'],'2026-10-24T00:00:00+00:00')

    def test_endpoint_validation_and_redirects_do_not_send_keys_elsewhere(self):
        for url in ('http://script.google.com/macros/s/abc/exec', 'https://evil.test/exec',
                    'https://script.google.com@evil.test/macros/s/abc/exec'):
            with self.assertRaises(ValueError):
                google_sync.Bridge({'url':url,'secret':'a'*64})
        import urllib.request
        handler = google_sync._GoogleRedirect()
        with self.assertRaises(google_sync.BridgeError):
            handler.redirect_request(urllib.request.Request('https://script.google.com/'),None,302,'',{},'https://evil.test/')

    def test_bridge_uses_verified_google_locale_and_preserves_signed_request(self):
        import hashlib
        import hmac
        import io
        import json

        settings = {'url':'https://script.google.com/macros/s/test/exec','secret':'a'*64}
        requests = []
        def open_request(request, timeout):
            requests.append(request)
            # Reproduce the observed Google failure on an English VPS.
            ready = request.get_header('Accept-language') == 'vi'
            return io.BytesIO(json.dumps({'ok':ready,'ready':ready}).encode())
        opener = Mock()
        opener.open.side_effect = open_request
        with patch.object(google_sync.urllib.request, 'build_opener', return_value=opener):
            self.assertTrue(google_sync.Bridge(settings).call('health')['ready'])
        request = requests[0]
        envelope = json.loads(request.data)
        self.assertEqual(json.loads(envelope['data']), {'action':'health'})
        message = f"{envelope['timestamp']}\n{envelope['nonce']}\n{envelope['data']}"
        self.assertEqual(envelope['signature'], hmac.new(
            settings['secret'].encode(), message.encode(), hashlib.sha256).hexdigest())
        self.assertNotIn(settings['secret'], request.data.decode())
        redirected = google_sync._GoogleRedirect().redirect_request(
            request, None, 303, '', {}, 'https://script.googleusercontent.com/macros/echo')
        self.assertEqual(redirected.get_header('Accept-language'), 'vi')
        self.assertIsNone(redirected.data)

    def reset_fixture(self):
        now = time.time()
        self.row['created_at'] = datetime.fromtimestamp(now-100, timezone.utc).isoformat()
        google_sync.apply_registration(self.db, self.row)
        return dict(response_id='reset-1', email=self.row['email'], identity=self.row['identity'],
                    password='new-private-password', created_at=datetime.fromtimestamp(now-50, timezone.utc).isoformat())

    def test_reset_verified_identity_revokes_sessions_preserves_profile_and_hashes(self):
        row = self.reset_fixture()
        token, before = accounts.login(self.db, self.row['identity'], self.row['password'])
        self.assertEqual(google_sync.apply_password_reset(self.db, row)['status'], 'accepted')
        self.assertIsNone(accounts.session(self.db, token))
        with self.assertRaises(ValueError):
            accounts.login(self.db, self.row['identity'], self.row['password'])
        _, after = accounts.login(self.db, self.row['identity'], row['password'])
        for key in ('identity','display_name','role','repository'):
            self.assertEqual(before[key], after[key])
        self.assertNotIn(row['password'].encode(), self.db.read_bytes())
        self.assertEqual(len(google_sync.pending_acks(self.db, password=True)), 1)

    def test_reset_wrong_email_identity_admin_or_manual_account_cannot_change_password(self):
        row = self.reset_fixture()
        accounts.create_user(self.db,'manual','manual-password-123')
        accounts.create_user(self.db,'admin','admin-password-123',role='admin')
        with accounts.database(self.db,write=True) as db:
            db.execute('INSERT INTO verified_google_users VALUES (?,?)',('admin','admin@vnu.edu.vn'))
        for i, changes in enumerate([{'email':'other@vnu.edu.vn'}, {'identity':'unknown'},
                                     {'email':'student@gmail.com'}, {'identity':'manual'},
                                     {'identity':'admin','email':'admin@vnu.edu.vn'}]):
            rejected = google_sync.apply_password_reset(self.db,{**row,**changes,'response_id':f'bad-{i}'})
            self.assertEqual(rejected['status'],'rejected')
        self.assertTrue(accounts.login(self.db,self.row['identity'],self.row['password']))

    def test_password_reset_after_expiry_keeps_the_seven_minute_wait(self):
        row=self.reset_fixture();start=time.time()
        accounts.login(self.db,self.row['identity'],self.row['password'],now=start)
        with patch('ottv2.google_sync.time.time',return_value=start+1800):
            self.assertEqual(google_sync.apply_password_reset(self.db,row)['status'],'accepted')
            with self.assertRaisesRegex(accounts.LoginLimited,'7 phút'):
                accounts.login(self.db,self.row['identity'],row['password'])

    def test_reset_replay_never_restores_old_password_or_revokes_new_session(self):
        row = self.reset_fixture()
        receipt = google_sync.apply_password_reset(self.db,row)
        newer = {**row,'response_id':'reset-2','password':'even-newer-password',
                 'created_at':datetime.fromtimestamp(time.time()-20,timezone.utc).isoformat()}
        self.assertEqual(google_sync.apply_password_reset(self.db,newer)['status'],'accepted')
        token,_ = accounts.login(self.db,self.row['identity'],newer['password'])
        self.assertEqual(google_sync.apply_password_reset(self.db,row),receipt)
        self.assertIsNotNone(accounts.session(self.db,token))
        self.assertIsNotNone(accounts.session(self.db,token))
        stale = {**row,'response_id':'late-old-request'}
        self.assertEqual(google_sync.apply_password_reset(self.db,stale)['status'],'rejected')

    def test_reset_submitted_before_portal_password_change_is_rejected(self):
        row = self.reset_fixture()
        accounts.change_password(self.db,self.row['identity'],'portal-password-new')
        self.assertEqual(google_sync.apply_password_reset(self.db,row)['status'],'rejected')
        self.assertTrue(accounts.login(self.db,self.row['identity'],'portal-password-new'))

    def test_reset_invalid_time_password_and_stale_submission_do_not_revoke_sessions(self):
        row = self.reset_fixture()
        token,_ = accounts.login(self.db,self.row['identity'],self.row['password'])
        for i, change in enumerate([{'created_at':None},{'created_at':'no-date'},
                                    {'created_at':'2026-01-01T00:00:00'},
                                    {'created_at':'2099-01-01T00:00:00Z'},
                                    {'created_at':self.row['created_at']},
                                    {'password':'short'},{'password':'x'*257}]):
            receipt = google_sync.apply_password_reset(self.db,{**row,**change,'response_id':str(i)})
            self.assertEqual(receipt['status'],'rejected')
        self.assertIsNotNone(accounts.session(self.db,token))

    def test_reset_ack_retry_does_not_apply_password_twice(self):
        row = self.reset_fixture()
        class FakeBridge:
            fail = True
            def call(self,action,**data):
                if action=='pull': return {'registrations':[]}
                if action=='pull_reset': return {'resets':[row]}
                if action in ('ack','ack_reset'):
                    if action=='ack_reset' and self.fail:
                        self.fail=False
                        raise google_sync.BridgeError('network')
                    return {'acknowledged':[r['response_id'] for r in data['receipts']]}
                return {'ok':True}
        bridge=FakeBridge()
        with self.assertRaises(google_sync.BridgeError): google_sync.sync_once(self.db,None,bridge)
        token,_=accounts.login(self.db,self.row['identity'],row['password'])
        google_sync.sync_once(self.db,None,bridge)
        self.assertIsNotNone(accounts.session(self.db,token))
        self.assertEqual(google_sync.pending_acks(self.db,password=True),[])

    def test_form_repository_change_preserves_password_session_profile_and_pending_update(self):
        row={**self.reset_fixture(),'change':'repository','password':'','repository':'https://github.com/new-owner/new-bot'}
        token,before=accounts.login(self.db,self.row['identity'],self.row['password'])
        with accounts.database(self.db,write=True) as db:
            db.execute("INSERT INTO updates(id,identity,repository,state,created) VALUES (?,?,?,'pending',?)",
                       ('already-pending',self.row['identity'],self.row['repository'],time.time()))
        receipt=google_sync.apply_password_reset(self.db,row)
        self.assertEqual(receipt['status'],'accepted')
        self.assertIsNotNone(accounts.session(self.db,token))
        after=accounts.session(self.db,token)
        self.assertEqual(after['repository'],row['repository'])
        for key in ('identity','display_name','role'):self.assertEqual(before[key],after[key])
        with accounts.database(self.db) as db:
            pending=db.execute('SELECT repository,state FROM updates').fetchone()
        self.assertEqual(tuple(pending),(self.row['repository'],'pending'))

    def test_form_both_changes_commit_together_and_retry_does_not_revoke_new_session(self):
        row={**self.reset_fixture(),'change':'both','repository':'https://github.com/new-owner/new-bot'}
        receipt=google_sync.apply_password_reset(self.db,row)
        self.assertEqual(receipt['status'],'accepted')
        token,user=accounts.login(self.db,self.row['identity'],row['password'])
        self.assertEqual(user['repository'],row['repository'])
        self.assertEqual(google_sync.apply_password_reset(self.db,row),receipt)
        self.assertIsNotNone(accounts.session(self.db,token))
        self.assertNotIn(row['password'].encode(),self.db.read_bytes())

    def test_invalid_or_blocked_repository_change_cannot_partially_change_password(self):
        row={**self.reset_fixture(),'change':'both','repository':'https://github.com/new-owner/new-bot'}
        token,_=accounts.login(self.db,self.row['identity'],self.row['password'])
        variants=[{'repository':'http://127.0.0.1/private'}, {'repository':''},
                  {'password':'short'}, {'change':'unknown'}, {'change':'password'},
                  {'change':'repository'}, {'email':'other@vnu.edu.vn'}]
        for index,variant in enumerate(variants):
            with self.subTest(variant=variant):
                receipt=google_sync.apply_password_reset(self.db,{**row,**variant,'response_id':f'invalid-both-{index}'})
                self.assertEqual(receipt['status'],'rejected')
        queue_admin.block_bot(self.queue,self.row['identity'])
        self.assertEqual(google_sync.apply_password_reset(self.db,row)['status'],'rejected')
        self.assertIsNotNone(accounts.session(self.db,token))
        user=accounts.session(self.db,token)
        self.assertEqual(user['repository'],self.row['repository'])

    def test_old_repository_form_cannot_overwrite_newer_website_or_form_change(self):
        row={**self.reset_fixture(),'change':'repository','password':'','repository':'https://github.com/old/bot'}
        accounts.set_repository(self.db,self.row['identity'],'https://github.com/website/bot')
        self.assertEqual(google_sync.apply_password_reset(self.db,row)['status'],'rejected')
        with patch('ottv2.google_sync.time.time',return_value=time.time()+10):
            newer={**row,'response_id':'newer-repository','created_at':datetime.now(timezone.utc).isoformat(),
                   'repository':'https://github.com/newer/bot'}
            self.assertEqual(google_sync.apply_password_reset(self.db,newer)['status'],'accepted')
        stale={**row,'response_id':'late-repository'}
        self.assertEqual(google_sync.apply_password_reset(self.db,stale)['status'],'rejected')
        _,user=accounts.login(self.db,self.row['identity'],self.row['password'])
        self.assertEqual(user['repository'],newer['repository'])

    def test_repository_ack_retry_keeps_latest_website_link(self):
        row={**self.reset_fixture(),'change':'repository','password':'','repository':'https://github.com/form/bot'}
        class Bridge:
            fail=True
            def call(self,action,**data):
                if action=='pull':return {'registrations':[]}
                if action=='pull_reset':return {'resets':[row]}
                if action=='ack_reset' and self.fail:
                    self.fail=False
                    raise google_sync.BridgeError('network')
                if action in ('ack','ack_reset'):return {'acknowledged':[r['response_id'] for r in data['receipts']]}
                return {'ok':True}
        bridge=Bridge()
        with self.assertRaises(google_sync.BridgeError):google_sync.sync_once(self.db,None,bridge)
        accounts.set_repository(self.db,self.row['identity'],'https://github.com/website/latest')
        google_sync.sync_once(self.db,None,bridge)
        _,user=accounts.login(self.db,self.row['identity'],self.row['password'])
        self.assertEqual(user['repository'],'https://github.com/website/latest')
        self.assertEqual(google_sync.pending_acks(self.db,password=True),[])

    def test_stale_repository_in_combined_request_does_not_change_either_field(self):
        row={**self.reset_fixture(),'change':'both','repository':'https://github.com/old/bot'}
        accounts.set_repository(self.db,self.row['identity'],'https://github.com/current/bot')
        token,_=accounts.login(self.db,self.row['identity'],self.row['password'])
        self.assertEqual(google_sync.apply_password_reset(self.db,row)['status'],'rejected')
        self.assertIsNotNone(accounts.session(self.db,token))
        user=accounts.session(self.db,token)
        self.assertEqual(user['repository'],'https://github.com/current/bot')

    def test_reinitializing_google_never_resets_repository_change_time(self):
        row={**self.reset_fixture(),'change':'repository','password':'','repository':'https://github.com/form/bot'}
        self.assertEqual(google_sync.apply_password_reset(self.db,row)['status'],'accepted')
        with accounts.database(self.db) as db:
            original=db.execute('SELECT changed_at FROM repository_changes WHERE identity=?',
                                (self.row['identity'],)).fetchone()[0]
        google_sync.initialize(self.db)
        with accounts.database(self.db) as db:
            self.assertEqual(db.execute('SELECT changed_at FROM repository_changes WHERE identity=?',
                                       (self.row['identity'],)).fetchone()[0],original)
