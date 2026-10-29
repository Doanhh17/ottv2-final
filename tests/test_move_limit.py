import copy
import json
from pathlib import Path
import unittest
from scripts.replay_fixtures import long_fixture, material_fixture
from ottv2 import cluster, elo_store
from ottv2.engine import Game, Piece, IllegalMove, small_sample_game
from ottv2.rules import CURRENT_LIMITS, CAPPED_DRAW_LIMITS, LEGACY_LIMITS, PREVIOUS_LIMITS, supports_limits, whole_wall_limit
from tests.test_local_worker import WorkerFixture


def replay_game(record):
    initial=record['initial_state']
    return Game({p:Piece(**v) for p,v in initial['board'].items()}, initial['goals'],
                set(initial['bombs']),set(initial['obstacles']),initial['turn'],
                max_valid_moves=record['limits'].get('max_valid_moves'),
                move_limit_tiebreak=record['limits'].get('move_limit_tiebreak','draw'))


class MoveLimitTests(unittest.TestCase):
    def test_current_policy_is_7_cpu_seconds_and_25_moves_for_each_side(self):
        self.assertEqual(CURRENT_LIMITS, {'cpu_budget_seconds':7.0,'response_limit_seconds':2.5,
            'memory_limit_mib_per_bot':64,'max_valid_moves':50,'move_limit_tiebreak':'piece_count'})
        for first in ('A','B'):
            record=long_fixture(first,limit=50,moves=50,preserve_material=True)
            self.assertEqual([sum(m['side']==s for m in record['history']) for s in ('A','B')],[25,25])
            self.assertEqual(record['result']['reason'],'move_limit')
        self.assertEqual(whole_wall_limit(7,50,2.5),140)

    def test_more_pieces_wins_at_70_for_either_starting_side(self):
        for first in ('A','B'):
            record=material_fixture(first)
            game=replay_game(record)
            for event in record['history']:
                game.play({key:event[key] for key in ('from','to')})
            self.assertEqual((game.result,game.reason),(first,'material_advantage'))
            self.assertEqual(len(game.history),70)
            self.assertEqual(sum(p.side==first for p in game.board.values()),9)
            self.assertEqual(sum(p.side!=first for p in game.board.values()),8)

    def test_legacy_capped_game_still_draws_with_unequal_pieces(self):
        record=material_fixture(tiebreak='draw')
        game=replay_game(record)
        for event in record['history']: game.play({key:event[key] for key in ('from','to')})
        self.assertEqual((game.result,game.reason),('draw','move_limit'))

    def test_last_capture_and_bomb_change_final_piece_count(self):
        base=small_sample_game()
        board={**base.board,'d4':Piece('A','rock'),'e5':Piece('B','scissors')}
        game=Game(board,base.goals,max_valid_moves=1,move_limit_tiebreak='piece_count')
        game.play({'from':'d4','to':'e5'})
        self.assertEqual((game.result,game.reason),('A','material_advantage'))
        game=Game({**base.board,'d4':Piece('A','rock')},base.goals,{'d5'},
                  max_valid_moves=1,move_limit_tiebreak='piece_count')
        game.play({'from':'d4','to':'d5'})
        self.assertEqual((game.result,game.reason),('draw','move_limit'))

    def test_ordinary_win_and_early_repetition_take_effect(self):
        base=small_sample_game()
        game=Game(base.board,{'A':'d1','B':'a9'},max_valid_moves=1,move_limit_tiebreak='piece_count')
        game.play({'from':'c1','to':'d1'})
        self.assertEqual((game.result,game.reason),('A','goal'))
        game=Game(base.board,base.goals,{'a2'},max_valid_moves=1,move_limit_tiebreak='piece_count')
        game.play({'from':'a1','to':'a2'})
        self.assertEqual((game.result,game.reason),('B','missing_kind'))
        game=Game({**base.board,'d1':Piece('A','rock')},base.goals,max_valid_moves=70,move_limit_tiebreak='piece_count')
        for a,b in [('a1','a2'),('i9','i8'),('a2','a1'),('i8','i9')]*2: game.play({'from':a,'to':b})
        self.assertEqual((game.result,game.reason),('draw','threefold_repetition'))

    def test_each_side_gets_35_valid_moves_including_b_first(self):
        for first in ('A','B'):
            record=long_fixture(first)
            game=replay_game(record)
            for i,event in enumerate(record['history']):
                if i==69:
                    with self.assertRaises(IllegalMove): game.play({'from':'e5','to':'e6'})
                    self.assertEqual(len(game.history),69)
                    self.assertIsNone(game.result)
                self.assertEqual(game.play({'from':event['from'],'to':event['to']}),event)
                if i<69: self.assertIsNone(game.result)
            self.assertEqual((game.result,game.reason),('draw','move_limit'))
            self.assertEqual([sum(m['side']==s for m in game.history) for s in ('A','B')],[35,35])
            with self.assertRaises(IllegalMove): game.play({k:record['history'][0][k] for k in ('from','to')})

    def test_win_on_last_move_takes_priority(self):
        base=small_sample_game()
        game=Game(base.board,{'A':'d1','B':'a9'},max_valid_moves=1)
        game.play({'from':'c1','to':'d1'})
        self.assertEqual((game.result,game.reason),('A','goal'))
        game=Game(base.board,base.goals,{'a2'},max_valid_moves=1)
        game.play({'from':'a1','to':'a2'})
        self.assertEqual((game.result,game.reason),('B','missing_kind'))

    def test_legacy_record_can_exceed_70_moves(self):
        record=long_fixture(limit=None,moves=101)
        game=replay_game(record)
        for event in record['history']: game.play({'from':event['from'],'to':event['to']})
        self.assertIsNone(game.result)
        self.assertEqual(len(game.history),101)

    def test_profiles_and_safeguard(self):
        for version in (1,2,3,4): self.assertTrue(supports_limits(LEGACY_LIMITS,version))
        self.assertFalse(supports_limits(PREVIOUS_LIMITS,1))
        for version in (2,3,4): self.assertTrue(supports_limits(PREVIOUS_LIMITS,version))
        for version in (1,2,3): self.assertFalse(supports_limits(CURRENT_LIMITS,version))
        for version in (3,4): self.assertTrue(supports_limits(CAPPED_DRAW_LIMITS,version))
        from ottv2.rules import MATERIAL_LIMITS
        self.assertTrue(supports_limits(MATERIAL_LIMITS,4))
        self.assertTrue(supports_limits(MATERIAL_LIMITS,5))
        self.assertFalse(supports_limits(CURRENT_LIMITS,4))
        self.assertFalse(supports_limits(CURRENT_LIMITS,5))
        self.assertTrue(supports_limits(CURRENT_LIMITS,6))
        for value in (True,0,-1,70.0,'70'):
            with self.assertRaises(ValueError):
                base=small_sample_game(); Game(base.board,base.goals,max_valid_moves=value)
        self.assertEqual(whole_wall_limit(10,70,2),155)
        self.assertEqual(whole_wall_limit(30),240)
        self.assertEqual(whole_wall_limit(10,70,2.5),190)


class CappedClusterTests(WorkerFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        cluster.enable(self.db)
        self.coordinator=cluster.Coordinator(self.db,self.results)
        cluster.register(self.db,'worker01','http://127.0.0.1:8765',self.root/'worker.json')

    def request(self, preserve_material=True):
        assignment=self.coordinator.call('worker01',{'action':'claim'},now=1000)
        job=assignment['job']
        path=self.root/'template.json'
        self.fake_runner(job,path)
        result=json.loads(path.read_bytes())
        game_record=long_fixture(job['first'],limit=job['limits']['max_valid_moves'],moves=job['limits']['max_valid_moves'],initial_state=job['initial_state'],preserve_material=preserve_material)
        for key in ('initial_state','final_state','history','result'): result[key]=game_record[key]
        return {'action':'complete','match_id':job['match_id'],
                'lease_token':assignment['lease_token'],'record':result}

    def test_capped_draw_scores_once_and_releases_both_players(self):
        request=self.request()
        self.assertTrue(self.coordinator.call('worker01',request,now=1001)['elo_applied'])
        self.assertFalse(self.coordinator.call('worker01',request,now=1002)['elo_applied'])
        self.assert_completed()
        self.assertTrue(all(row['draws']==1 and row['rating']==1000 for row in elo_store.standings(self.elo)))

    def test_v5_worker_cannot_claim_7_second_jobs_but_old_70_move_result_still_scores(self):
        from ottv2 import match_queue
        from ottv2.rules import RESPONSE_25_LIMITS
        self.assertEqual(self.coordinator.call('worker01',{'action':'claim'},now=1000,rules_version=5),
                         {'state':'idle','reason':'worker_update_required'})
        with match_queue._write(self.db) as db:
            job=json.loads(db.execute('SELECT payload FROM jobs WHERE match_id=?',(self.match_id,)).fetchone()[0])
            job['limits']=dict(RESPONSE_25_LIMITS)
            db.execute('UPDATE jobs SET payload=? WHERE match_id=?',(json.dumps(job),self.match_id))
        assignment=self.coordinator.call('worker01',{'action':'claim'},now=1001,rules_version=5)
        output=self.root/'old-v5.json';self.fake_runner(assignment['job'],output)
        record=json.loads(output.read_bytes())
        fixture=material_fixture(job['first'],initial_state=job['initial_state'],limit=70)
        for key in ('initial_state','final_state','history','result'):record[key]=fixture[key]
        request={'action':'complete','match_id':self.match_id,'lease_token':assignment['lease_token'],'record':record}
        self.assertTrue(self.coordinator.call('worker01',request,now=1002,rules_version=5)['elo_applied'])
        self.assertEqual(len(record['history']),70)

    def test_material_win_is_replayed_scored_once_and_cannot_be_forged(self):
        request=self.request()
        record=request['record']
        fixture=material_fixture(record['initial_state']['turn'],initial_state=record['initial_state'],limit=record['limits']['max_valid_moves'])
        for key in ('initial_state','final_state','history','result'):record[key]=fixture[key]
        fake=copy.deepcopy(request)
        fake['record']['result']['winner']='B' if record['result']['winner']=='A' else 'A'
        with self.assertRaises(ValueError):self.coordinator.call('worker01',fake,now=1001)
        self.assertTrue(self.coordinator.call('worker01',request,now=1002)['elo_applied'])
        self.assertFalse(self.coordinator.call('worker01',request,now=1003)['elo_applied'])
        rows=elo_store.standings(self.elo)
        self.assertEqual(sorted(row['rating'] for row in rows),[984,1016])
        self.assertEqual(sum(row['wins'] for row in rows),1)
        self.assertEqual(sum(row['draws'] for row in rows),0)

    def test_v3_worker_keeps_old_capped_job_and_is_blocked_from_new_job(self):
        self.assertEqual(self.coordinator.call('worker01',{'action':'claim'},now=1000,rules_version=3),
                         {'state':'idle','reason':'worker_update_required'})
        from ottv2 import match_queue
        with match_queue._write(self.db) as db:
            job=json.loads(db.execute('SELECT payload FROM jobs WHERE match_id=?',(self.match_id,)).fetchone()[0])
            job['limits']=dict(CAPPED_DRAW_LIMITS)
            db.execute('UPDATE jobs SET payload=? WHERE match_id=?',(json.dumps(job),self.match_id))
        assignment=self.coordinator.call('worker01',{'action':'claim'},now=1001,rules_version=3)
        target=self.root/'old-capped.json';self.fake_runner(assignment['job'],target)
        record=json.loads(target.read_bytes())
        fixture=material_fixture(job['first'],tiebreak='draw',initial_state=job['initial_state'])
        for key in ('initial_state','final_state','history','result'):record[key]=fixture[key]
        request={'action':'complete','match_id':self.match_id,'lease_token':assignment['lease_token'],'record':record}
        self.assertEqual(record['result']['reason'],'move_limit')
        self.assertTrue(self.coordinator.call('worker01',request,now=1002,rules_version=3)['elo_applied'])

    def test_premature_draw_and_extra_move_are_rejected(self):
        request=self.request()
        early=copy.deepcopy(request)
        early['record']['history'].pop()
        early['record']['result']['valid_move_count']=69
        with self.assertRaises(ValueError): self.coordinator.call('worker01',early,now=1001)
        extra=copy.deepcopy(request)
        extra['record']['history'].append(copy.deepcopy(extra['record']['history'][0]))
        extra['record']['result']['valid_move_count']=71
        with self.assertRaises(ValueError): self.coordinator.call('worker01',extra,now=1001)
        self.assertTrue(self.coordinator.call('worker01',request,now=1002)['elo_applied'])

    def test_v2_worker_cannot_claim_new_game_but_can_complete_old_game(self):
        self.assertEqual(self.coordinator.call('worker01',{'action':'claim'},now=1000,rules_version=2),
                         {'state':'idle','reason':'worker_update_required'})
        from ottv2 import match_queue
        with match_queue._write(self.db) as db:
            job=json.loads(db.execute('SELECT payload FROM jobs WHERE match_id=?',(self.match_id,)).fetchone()[0])
            job['limits']=dict(PREVIOUS_LIMITS)
            db.execute('UPDATE jobs SET payload=? WHERE match_id=?',(json.dumps(job),self.match_id))
        assignment=self.coordinator.call('worker01',{'action':'claim'},now=1001,rules_version=2)
        self.assertEqual(assignment['job']['limits'],PREVIOUS_LIMITS)
        target=self.root/'previous.json';self.fake_runner(assignment['job'],target)
        request={'action':'complete','match_id':self.match_id,'lease_token':assignment['lease_token'],
                 'record':json.loads(target.read_bytes())}
        self.assertTrue(self.coordinator.call('worker01',request,now=1002,rules_version=2)['elo_applied'])
