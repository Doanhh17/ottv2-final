from http.client import HTTPConnection
import json
import re
from pathlib import Path
import sqlite3
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from ottv2 import accounts, bot_store, daily_quota, elo_store, local_arena, local_worker, log_retention, match_queue, portal_api, portal_data, queue_admin, update_gate
from tests.test_local_worker import WorkerFixture


class PortalTests(WorkerFixture, unittest.TestCase):
    def test_status_keeps_quota_and_pairing_consistent_during_cancellation(self):
        self.login()
        attempted, finished = Event(), Event()
        errors=[]
        def cancel():
            attempted.set()
            try:match_queue.cancel_job(self.db,self.match_id)
            except BaseException as error:errors.append(error)
            finally:finished.set()
        writer=Thread(target=cancel,daemon=True)
        original=daily_quota.status_from_connection
        def interleave(connection,identity,now=None):
            quota=original(connection,identity,now)
            writer.start()
            self.assertTrue(attempted.wait(2))
            # The cancellation cannot commit while this queue read is open.
            self.assertFalse(finished.wait(.05))
            return quota
        try:
            with patch.object(daily_quota,'status_from_connection',side_effect=interleave):
                code,_,before=self.request('/api/status')
        finally:
            if writer.ident is not None:writer.join(5)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors,[])
        self.assertEqual(code,200)
        self.assertEqual(before['request']['state'],'matched')
        self.assertEqual(before['quota']['active']['reserved'],1)
        code,_,after=self.request('/api/status')
        self.assertEqual(code,200)
        self.assertEqual(after['request']['state'],'idle')
        self.assertEqual(after['quota']['active']['reserved'],0)

    def test_light_status_omits_history_and_history_is_scoped(self):
        self.login()
        with patch('ottv2.portal_data.history',side_effect=AssertionError('should be lazy')), patch('ottv2.bot_store.load_version',side_effect=AssertionError('no source read')):
            code,_,data = self.request('/api/status')
        self.assertEqual(code,200)
        self.assertNotIn('matches',data)
        self.assertEqual(data['latest_match']['match_id'],self.match_id)
        self.assertEqual(len(self.request('/api/history')[2]['matches']),1)
        self.login('student03')
        self.assertEqual(self.request('/api/history')[2]['matches'],[])

    def test_github_limit_defers_the_whole_queue_without_charging_again(self):
        from ottv2.github_update import GitHubRateLimited
        old = bot_store.load_bot(self.store,'student01')[1]['version']
        first = portal_data.request_update(self.db,self.accounts,'student01','https://github.com/a/b')
        portal_data.request_update(self.db,self.accounts,'student02','https://github.com/a/b')
        with patch('ottv2.github_update.download_repository',side_effect=GitHubRateLimited(time.time()+600)) as network:
            self.assertEqual(portal_data.process_update(self.db,self.accounts)['state'],'bot_update_deferred')
            self.assertIsNone(portal_data.process_update(self.db,self.accounts))
            self.assertEqual(network.call_count,1)
        self.assertEqual(portal_data.latest_update(self.accounts,'student01')['id'],first['id'])
        self.assertEqual(portal_data.latest_update(self.accounts,'student01')['state'],'pending')
        self.assertEqual(bot_store.load_bot(self.store,'student01')[1]['version'],old)

    def setUp(self):
        super().setUp()
        self.accounts = self.root / 'accounts.sqlite3'
        accounts.initialize(self.accounts, self.db)
        for identity in ('student01', 'student02', 'student03', 'admin'):
            accounts.create_user(self.accounts, identity, 'test-password-123', role='admin' if identity == 'admin' else 'student')
        self.server = portal_api.create_server(self.store, 0, self.results, self.db, self.accounts)
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.cookie = ''
        self.csrf = ''

    def stop(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()

    def request(self, path, body=None, *, method=None, headers=None):
        port = self.server.server_port
        client = HTTPConnection('127.0.0.1', port, timeout=5)
        hdr = {'Cookie': self.cookie}
        if body is not None:
            hdr.update({'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}', 'X-CSRF-Token': self.csrf})
        hdr.update(headers or {})
        try:
            client.request(method or ('POST' if body is not None else 'GET'), path,
                           json.dumps(body) if isinstance(body, dict) else body, hdr)
            response = client.getresponse()
            raw = response.read()
            parsed = json.loads(raw) if raw and response.getheader('Content-Type', '').startswith('application/json') else raw
            return response.status, dict(response.getheaders()), parsed
        finally:
            client.close()

    def login(self, identity='student01'):
        if self.cookie:
            self.request('/api/logout',{})
            self.cookie='';self.csrf=''
        code, headers, answer = self.request('/api/login', {'identity': identity, 'password': 'test-password-123'})
        self.assertEqual(code, 200, answer)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.csrf = answer['user']['csrf']
        return headers

    def test_page_is_public_but_all_data_requires_login_and_legacy_routes_are_closed(self):
        code, headers, body = self.request('/')
        self.assertEqual(code, 200)
        self.assertIn('frame-ancestors', headers['Content-Security-Policy'])
        self.assertIn('Đăng nhập'.encode(), body)
        for path in ('/api/me', '/api/logs/' + self.match_id, '/matches', '/bots/student02', '/queue', '/quota/student02'):
            self.assertEqual(self.request(path)[0], 401, path)
        self.login()
        for path in ('/matches', '/bots/student02', '/queue', '/quota/student02'):
            self.assertEqual(self.request(path)[0], 404, path)

    def test_login_cookie_logout_and_no_plaintext_password_in_database(self):
        headers = self.login()
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertNotIn(b'test-password-123', self.accounts.read_bytes())
        self.assertEqual(self.request('/api/me')[2]['user']['identity'], 'student01')
        self.assertEqual(self.request('/api/me')[2]['limits'],
                         {'cpu_seconds':7.0,'response_seconds':2.5,'memory_mib':64,'max_moves_per_side':25})
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(self.request('/api/me')[0], 401)

    def test_public_practice_resources_do_not_expose_account_or_worker_data(self):
        code,headers,body=self.request('/api/practice-config')
        self.assertEqual(code,200)
        bundle=body
        self.assertEqual(set(bundle),{'schema_version','type','map','bots','limits','config_sha256'})
        self.assertEqual(bundle['limits']['cpu_budget_seconds'],7)
        self.assertEqual(bundle['limits']['max_valid_moves'],50)
        self.assertEqual(set(bundle['bots']),{'beginner','intermediate','advanced'})
        for path in ('/practice.html','/replay.html'):
            code,headers,body=self.request(path)
            self.assertEqual(code,200)
            self.assertIn('attachment',headers['Content-Disposition'])
            self.assertIn(b'<!doctype html>',body[:100].lower())

    def test_admin_map_and_three_bots_are_validated_and_published_together(self):
        self.login('admin')
        new_map=(Path(__file__).resolve().parent.parent/'maps/pool-examples/open.json').read_text()
        bots=dict.fromkeys(('beginner','intermediate','advanced'),'print("ready")')
        code,_,body=self.request('/api/admin/map',{'map':'broken','practice_bots':bots})
        self.assertEqual(code,400)
        self.assertIn('Tạm dừng',body['error'])
        self.request('/api/admin/pause',{'paused':True,'reason':''})
        code,_,_=self.request('/api/admin/map',{'map':new_map,'practice_bots':{**bots,'advanced':'if :'}})
        self.assertEqual(code,400)
        self.assertNotEqual(self.request('/api/practice-config')[2]['map']['name'],'open')
        self.assertEqual(self.request('/api/admin/map',{'map':new_map,'practice_bots':bots})[0],200)
        exported=self.request('/api/practice-config')[2]
        self.assertEqual(exported['map']['name'],'open')
        self.assertEqual(exported['bots'],bots)

    def test_school_logo_is_public_cached_and_bundled_with_portal(self):
        code, headers, image = self.request('/assets/uet-logo.png')
        self.assertEqual(code, 200)
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(headers['Cache-Control'], 'public, max-age=86400')
        self.assertTrue(image.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(image, (Path(__file__).resolve().parent.parent / 'web/assets/uet-logo.png').read_bytes())
        self.assertEqual(self.request('/assets/missing.png')[0], 401)
        self.assertEqual(self.request('/assets/../README.md')[0], 401)

    def test_origin_csrf_host_and_body_boundaries_reject_writes(self):
        self.login()
        before = self.db.read_bytes()
        for headers in ({'Origin': 'https://evil.example'}, {'X-CSRF-Token': 'bad'},
                        {'Content-Type': 'text/plain'}, {'Host': 'evil.example'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            self.assertIn(self.request('/api/find', {}, headers=headers)[0], (400, 403))
        self.assertEqual(self.request('/api/find?bot=student02', {})[0], 400)
        self.assertEqual(self.request('/api/find', {'identity': 'student02'})[0], 404)
        self.assertEqual(self.request('/api/find', '{}', headers={'Content-Length': str(portal_api.MAX_BODY + 1)})[0], 400)
        self.assertEqual(self.request('/api/find', '{"x":1,"x":2}')[0], 400)
        self.assertEqual(before, self.db.read_bytes())

    def test_identity_cannot_be_spoofed_and_other_students_logs_are_hidden(self):
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.login('student03')
        self.assertEqual(self.request('/api/me')[2]['matches'], [])
        self.assertEqual(self.request('/api/logs/' + self.match_id)[0], 404)
        self.login('student01')
        code, headers, log = self.request('/api/logs/' + self.match_id)
        self.assertEqual(code, 200)
        self.assertEqual(log['match_id'], self.match_id)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn(str(self.store), json.dumps(self.request('/api/me')[2]))

    def test_find_duplicate_cancel_and_completed_match_quota(self):
        match_queue.cancel_job(self.db, self.match_id)
        self.login()
        self.assertEqual(self.request('/api/find', {})[2]['state'], 'waiting')
        self.assertFalse(self.request('/api/find', {})[2]['created'])
        self.assertEqual(self.request('/api/me')[2]['quota']['active']['reserved'], 0)
        self.assertTrue(self.request('/api/cancel', {})[2]['cancelled'])
        self.request('/api/find', {})
        match_queue.dispatch(self.db)
        self.assertEqual(self.request('/api/cancel', {})[0], 400)
        pending = self.request('/api/me')[2]
        self.assertEqual(pending['request']['job_state'], 'pending')
        self.assertGreaterEqual(pending['request']['wait_seconds'], 0)
        self.assertGreaterEqual(pending['matches'][0]['wait_seconds'], 0)
        with patch.object(portal_data.time, 'time', return_value=time.time() + 601):
            delayed = self.request('/api/me')[2]
        self.assertGreaterEqual(delayed['request']['wait_seconds'], 600)
        self.assertGreaterEqual(delayed['matches'][0]['wait_seconds'], 600)
        local_worker.run_once(self.db, self.results, self.fake_runner)
        data = self.request('/api/me')[2]
        self.assertEqual(data['quota']['active']['used'], 1)
        self.assertTrue(data['matches'][0]['log_available'])
        self.assertNotIn('ratings',data)
        self.assertEqual(data['elo'],next(row['rating'] for row in elo_store.standings(self.elo) if row['bot_id']=='student01'))

    def test_expired_log_returns_clear_410_only_to_owner_and_keeps_result(self):
        local_worker.run_once(self.db,self.results,self.fake_runner)
        with match_queue._write(self.db) as db:
            saved=log_retention.metadata(db,self.match_id)
        log_retention.prune(self.db,self.results,apply=True,now=saved['expires_at'])
        self.login('student01')
        code,_,body=self.request('/api/logs/'+self.match_id)
        self.assertEqual(code,410)
        self.assertIn('7 ngày',body['error'])
        row=self.request('/api/me')[2]['matches'][0]
        self.assertTrue(row['log_expired'])
        self.assertFalse(row['log_available'])
        self.assertIsNotNone(row['result'])
        self.login('student03')
        self.assertEqual(self.request('/api/logs/'+self.match_id)[0],404)

    def test_separate_page_routes_and_current_map_svg_follow_map_change(self):
        for route in ('/arena','/bot','/results','/guide','/account','/manage','/students','/system'):
            self.assertEqual(self.request(route)[0],200)
        code,headers,_=self.request('/standings')
        self.assertEqual(code,303)
        self.assertTrue(headers['Location'].startswith('https://docs.google.com/spreadsheets/'))
        code,headers,svg=self.request('/board.svg')
        self.assertEqual(code,200)
        self.assertIn('image/svg+xml',headers['Content-Type'])
        self.assertIn(b'viewBox',svg)
        self.assertEqual(svg.count(b'<g>'),18)
        old=portal_data.config(self.db)['map']['sha256']
        self.login('admin')
        self.request('/api/admin/pause',{'paused':True,'reason':''})
        updated=(Path(__file__).resolve().parent.parent/'maps/pool-examples/open.json').read_text()
        self.request('/api/admin/map',{'map':updated})
        self.assertEqual(self.request('/board/'+old+'.svg')[0],409)
        self.assertNotEqual(self.request('/board.svg')[2],svg)

    def test_public_https_checks_origin_host_and_marks_cookie_secure(self):
        from http.client import HTTPConnection
        server=portal_api.create_server(self.store,0,self.results,self.db,self.accounts,public_origin='https://ott.example.org')
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            payload=json.dumps({'identity':'student01','password':'test-password-123'})
            headers={'Host':'ott.example.org','Origin':'https://ott.example.org','Content-Type':'application/json'}
            conn.request('POST','/api/login',payload,headers)
            response=conn.getresponse();response.read()
            self.assertEqual(response.status,200);self.assertIn('Secure',response.getheader('Set-Cookie'))
            headers['Origin']='http://ott.example.org'
            conn.request('POST','/api/login',payload,headers);response=conn.getresponse();response.read()
            self.assertEqual(response.status,403)
            conn.close()
        finally:
            server.shutdown();thread.join(2);server.server_close()
        accounts.change_password(self.accounts,'admin','OTTv2-demo-2026')
        with self.assertRaisesRegex(ValueError,'demo'):
            portal_api.create_server(self.store,0,self.results,self.db,self.accounts,public_origin='https://ott.example.org')

    def test_student_cannot_use_any_admin_action(self):
        self.login()
        for path, body in (('pause', {'paused': True, 'reason': ''}), ('updates', {'locked': True}),
                           ('block', {'identity': 'student02', 'blocked': True, 'reason': ''}),
                           ('student', {'identity': 'student04', 'password': 'valid-password', 'display_name': 'Four'})):
            self.assertEqual(self.request('/api/admin/' + path, body)[0], 403)
        self.assertFalse(queue_admin.status(self.db)['control']['paused'])

    def test_admin_map_change_preserves_created_job_and_requires_pause(self):
        self.login('admin')
        new_map = (Path(__file__).resolve().parent.parent / 'maps/pool-examples/open.json').read_text()
        self.assertEqual(self.request('/api/admin/map', {'map': new_map})[0], 400)
        self.assertEqual(self.request('/api/admin/pause', {'paused': True, 'reason': 'Đổi bản đồ'})[0], 200)
        self.assertEqual(self.request('/api/admin/map', {'map': new_map})[0], 200)
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['map'], self.job['map'])
        self.assertTrue(queue_admin.status(self.db)['control']['paused'])
        self.assertEqual(self.request('/api/admin/map', {'map': '{}'})[0], 400)

    def test_admin_block_and_update_lock_applied_on_server(self):
        match_queue.cancel_job(self.db, self.match_id)
        self.login('admin')
        self.request('/api/admin/block', {'identity': 'student01', 'blocked': True, 'reason': 'check'})
        self.request('/api/admin/updates', {'locked': True})
        self.login()
        self.assertTrue(self.request('/api/me')[2]['blocked'])
        self.assertEqual(self.request('/api/find', {})[0], 400)
        accounts.set_repository(self.accounts, 'student01', 'https://github.com/example/bot')
        self.assertEqual(self.request('/api/update', {})[0], 400)
        self.assertFalse((self.store / 'student01' / 'update-request.json').exists())

    def test_unused_direct_account_writes_are_closed_and_admin_reset_revokes_sessions(self):
        self.login()
        cookie,csrf=self.cookie,self.csrf
        self.assertEqual(self.request('/api/password', {'password':'new-password-123'})[0],404)
        self.assertEqual(self.request('/api/repository', {'repository':'https://github.com/new/bot'})[0],404)
        self.assertEqual(self.request('/api/me')[0],200)
        self.login('admin')
        self.assertEqual(self.request('/api/admin/password', {'identity':'student01','password':'test-password-123'})[0],200)
        self.cookie,self.csrf=cookie,csrf
        self.assertEqual(self.request('/api/me')[0],401)
        self.login()

    def test_admin_password_cannot_change_through_web_or_student_reset(self):
        self.login('admin')
        self.assertEqual(self.request('/api/password', {'password':'new-admin-password-123'})[0], 404)
        self.assertEqual(self.request('/api/admin/password', {'identity':'admin','password':'new-admin-password-123'})[0], 400)
        self.assertEqual(self.request('/api/me')[0], 200)
        self.login('admin')

    def test_admin_map_document_is_protected_and_all_examples_are_valid(self):
        self.assertEqual(self.request('/api/admin/map-guide')[0], 401)
        self.login()
        self.assertEqual(self.request('/api/admin/map-guide')[0], 403)
        self.login('admin')
        code, headers, guide = self.request('/api/admin/map-guide')
        self.assertEqual(code, 200)
        self.assertIn('attachment',headers['Content-Disposition'])
        from ottv2.map_config import load_map
        page=(Path(__file__).resolve().parent.parent/'web/portal.html').read_text(encoding='utf-8')
        self.assertNotIn('id="map-example"',page)
        self.assertIn('Hướng dẫn bản đồ JSON',page)
        examples=[re.search(r'```json\n(.*?)\n```',guide.decode(),re.S).group(1)]
        for index, source in enumerate(examples):
            path=self.root/f'map-example-{index}.json';path.write_text(source,encoding='utf-8')
            game,_=load_map(path)
            self.assertEqual(len(game.board),18)

    def test_admin_create_account_and_nonexistent_bot_can_open_page(self):
        self.login('admin')
        body = {'identity': 'student04', 'display_name': '<script>text only</script>', 'password': 'test-password-123'}
        self.assertEqual(self.request('/api/admin/student', body)[0], 200)
        self.assertEqual(self.request('/api/admin/student', body)[0], 400)
        self.login('student04')
        data = self.request('/api/me')[2]
        self.assertIsNone(data['bot'])
        self.assertEqual(data['quota']['active']['remaining'], 3)
        self.assertEqual(data['poll_after_seconds'],60)
        self.assertEqual(self.request('/api/find', {})[0], 400)

    def test_account_lock_revokes_sessions_denies_login_and_unlock_keeps_existing_job(self):
        self.login()
        student_cookie = self.cookie
        accounts.set_repository(self.accounts, 'student01', 'https://github.com/student/bot')
        with accounts.database(self.accounts, write=True) as db:
            db.execute("INSERT INTO updates(id,identity,repository,state,created) VALUES ('waiting-update','student01',?,'pending',?)",
                       ('https://github.com/student/bot', time.time()))
        self.login('admin')
        body = {'identity': 'student01', 'locked': True, 'reason': 'Kiểm tra'}
        admin_cookie,admin_csrf=self.cookie,self.csrf
        self.assertEqual(self.request('/api/admin/account-lock', body)[0], 200)
        self.assertTrue(next(u for u in self.request('/api/me')[2]['students'] if u['identity']=='student01')['locked'])
        self.assertEqual(self.request('/api/admin/account-lock', body)[0], 200)
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'pending')
        self.assertEqual(portal_data.latest_update(self.accounts, 'student01')['state'], 'failed')
        self.cookie = student_cookie
        self.assertEqual(self.request('/api/me')[0], 401)
        self.assertEqual(self.request('/api/login', {'identity':'student01','password':'test-password-123'})[0], 400)
        self.cookie,self.csrf=admin_cookie,admin_csrf
        self.assertEqual(self.request('/api/admin/account-lock', {**body, 'locked':False})[0], 200)
        self.cookie = student_cookie
        self.assertEqual(self.request('/api/me')[0], 401)
        self.login()
        self.assertEqual(self.request('/api/me')[2]['user']['repository'], 'https://github.com/student/bot')
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'pending')

    def test_account_lock_cancels_waiting_and_legacy_unblock_cannot_bypass(self):
        match_queue.cancel_job(self.db, self.match_id)
        queue_admin.block_bot(self.db, 'student02')
        self.assertEqual(match_queue.find_match(self.db, 'student01')['state'], 'waiting')
        answer = portal_data.set_student_locked(self.db, self.accounts, 'student01', True)
        self.assertEqual(answer['cancelled_waiting'], 1)
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'idle')
        with self.assertRaises(ValueError):
            match_queue.find_match(self.db, 'student01')
        queue_admin.block_bot(self.db, 'student02', False)
        self.assertEqual(match_queue.find_match(self.db, 'student02')['state'], 'waiting')
        with self.assertRaises(ValueError):
            queue_admin.block_bot(self.db, 'student01', False)
        portal_data.set_student_locked(self.db, self.accounts, 'student01', False)
        self.assertEqual(match_queue.find_match(self.db, 'student01')['state'], 'matched')

    def test_only_admin_can_lock_students_and_bad_requests_leave_accounts_unchanged(self):
        body = {'identity': 'student02', 'locked': True, 'reason': ''}
        self.login()
        self.assertEqual(self.request('/api/admin/account-lock', body)[0], 403)
        self.login('admin')
        for changes in ({'identity':'admin'}, {'identity':'missing'}, {'locked':1}, {'reason':'x'*201}):
            self.assertEqual(self.request('/api/admin/account-lock', {**body, **changes})[0], 400)
        self.assertNotIn('student02', queue_admin.status(self.db)['control']['blocked_bots'])
        self.login('student02')

    def test_account_lock_rolls_back_sessions_and_account_state_if_queue_write_fails(self):
        token, _ = accounts.login(self.accounts, 'student01', 'test-password-123')
        with match_queue._write(self.db) as db:
            db.execute("CREATE TRIGGER fail_account_lock BEFORE UPDATE ON config BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            portal_data.set_student_locked(self.db, self.accounts, 'student01', True)
        self.assertIsNotNone(accounts.session(self.accounts, token))
        self.assertNotIn('student01', queue_admin.status(self.db)['control']['blocked_bots'])

    def test_admin_created_student_can_login_with_bound_repository_without_google_verification(self):
        self.login('admin')
        body = {'identity': 'student04', 'display_name': 'Sinh viên mới',
                'password': 'test-password-123', 'repository': 'https://github.com/student/bot'}
        self.assertEqual(self.request('/api/admin/student', body)[0], 200)
        self.login('student04')
        data = self.request('/api/me')[2]
        self.assertEqual(data['user']['repository'], body['repository'])
        self.assertEqual(self.request('/api/admin/student', {**body, 'identity': 'student05'})[0], 403)
        self.login('admin')
        for invalid in ('http://127.0.0.1/private', [], 3):
            with self.subTest(repository=invalid):
                self.assertEqual(self.request('/api/admin/student', {**body, 'identity': 'student05', 'repository': invalid})[0], 400)
        with accounts.database(self.accounts) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM users WHERE identity=?', ('student05',)).fetchone())

    def test_repository_bound_to_login_and_queued_update_does_not_block_http(self):
        self.login()
        for value in (None, 123, [], {}, 'x' * 251):
            self.assertEqual(self.request('/api/repository', {'repository': value})[0], 404)
        self.assertEqual(self.request('/api/repository', {'repository': 'http://127.0.0.1/private'})[0], 404)
        accounts.set_repository(self.accounts,'student01','https://github.com/example/bot')
        with patch('ottv2.github_update.download_repository') as download:
            self.assertEqual(self.request('/api/update', {})[0], 200)
            self.assertEqual(self.request('/api/update', {})[0], 400)
            download.assert_not_called()
        data = self.request('/api/me')[2]
        self.assertEqual(data['update']['state'], 'pending')
        self.assertGreater(data['cooldown_seconds'], 0)
        self.assertFalse((self.store / 'student01' / '.request.lock').exists())

    def test_update_failure_preserves_old_bot_and_not_repeated_automatically(self):
        old = bot_store.load_bot(self.store, 'student01')[1]['version']
        portal_data.request_update(self.db, self.accounts, 'student01', 'https://github.com/example/bot')
        with patch('ottv2.github_update.download_repository', side_effect=ValueError('bad repository')) as download:
            result = portal_data.process_update(self.db, self.accounts)
            self.assertEqual(result['state'], 'bot_update_failed')
            self.assertIsNone(portal_data.process_update(self.db, self.accounts))
            self.assertEqual(download.call_count, 1)
        self.assertEqual(bot_store.load_bot(self.store, 'student01')[1]['version'], old)
        self.assertGreater(update_gate.update_status(self.store, 'student01')['remaining_seconds'], 0)

    def test_first_update_failure_keeps_new_account_page_usable(self):
        self.login('student03')
        portal_data.request_update(self.db, self.accounts, 'student03', 'https://github.com/example/bot')
        code, _, data = self.request('/api/me')
        self.assertEqual(code, 200)
        self.assertIsNone(data['bot'])
        self.assertEqual(data['update']['state'], 'pending')
        with patch('ottv2.github_update.download_repository', side_effect=ValueError('bad repository')):
            portal_data.process_update(self.db, self.accounts)
        self.assertEqual(self.request('/api/me')[0], 200)
        self.assertEqual(self.request('/api/me')[2]['update']['state'], 'failed')

    def test_accepted_update_finishes_after_lock_and_interrupted_operation_is_visible(self):
        portal_data.request_update(self.db, self.accounts, 'student01', 'https://github.com/example/bot')
        queue_admin.set_updates_locked(self.db, True)
        origin = {'type': 'github', 'repository': 'https://github.com/example/bot', 'commit': 'a' * 40}
        with patch('ottv2.github_update.download_repository', return_value=((self.root / 'bot.zip').read_bytes(), origin)):
            self.assertEqual(portal_data.process_update(self.db, self.accounts)['state'], 'bot_updated')
        with accounts.database(self.accounts, write=True) as connection:
            connection.execute("UPDATE updates SET state='running'")
        self.assertIsNone(portal_data.process_update(self.db, self.accounts))
        self.assertEqual(portal_data.latest_update(self.accounts, 'student01')['state'], 'failed')

    def test_list_has_ten_own_jobs_and_does_not_expose_another_users_jobs(self):
        match_queue.cancel_job(self.db, self.match_id)
        for _ in range(12):
            match_queue.find_match(self.db, 'student01')
            job = match_queue.find_match(self.db, 'student02')
            match_queue.cancel_job(self.db, job['match_id'])
        self.login()
        self.assertEqual(len(self.request('/api/me')[2]['matches']), 10)
        self.login('student03')
        self.assertEqual(len(self.request('/api/me')[2]['matches']), 0)

    def test_portal_arena_runs_maintenance_between_matches_and_closes(self):
        events = []
        def emit(event):
            events.append(event)
        with patch('ottv2.portal_data.process_update', return_value={'state': 'bot_updated'}) as process:
            result = local_arena.run_arena(self.db, self.results, port=0, max_cycles=1,
                                           account_db=self.accounts, runner=self.fake_runner, emit=emit)
            process.assert_called_once_with(self.db, self.accounts)
        self.assertEqual(result['completed'], 1)
        self.assertEqual(events[0]['mode'], 'local_accounts')
        self.assertEqual(events[1]['state'], 'bot_updated')
