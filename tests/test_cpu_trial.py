from pathlib import Path
import hashlib
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
from functools import partial
import unittest
from unittest.mock import patch

from ottv2.cpu_trial import run_trial as current_trial
from ottv2.cpu_budget import CpuBudgetExceeded
from ottv2.engine import FormatError, Game, Piece, small_sample_game as sample_game
from ottv2.isolated_session import BOX_ROOT, MEMORY_LIMIT_MIB, IsolatedSampleSession
from ottv2.sample_session import ROOT
from ottv2.protocol import state_message

# Retain old-budget fault and replay scenarios as explicit compatibility tests.
run_trial = partial(current_trial, cpu_budget=5.0, response_timeout=1.0, max_valid_moves=None)


PROBE = """
import json, os, random, signal, sys, time
sys.path.insert(0, '/box')
from ottv2.bot_process import choose_move
mode = sys.argv[1]
startup = float(sys.argv[2])
def exhaust_memory():
    blocks = []
    for _ in range(64):
        blocks.append(bytearray(b'x' * (4 * 1024 * 1024)))
    raise RuntimeError('probe unexpectedly survived bounded memory allocation')

if mode == 'startup_exit':
    sys.exit(7)
if mode == 'startup_oom':
    exhaust_memory()
if mode == 'startup_crash':
    raise RuntimeError('probe startup crash')
if mode == 'startup_sleep':
    time.sleep(60)
if mode == 'startup_json':
    print('{oops', flush=True)
    time.sleep(60)
if mode == 'startup_schema':
    print(json.dumps({'protocol_version': 2, 'type': 'ready'}), flush=True)
    time.sleep(60)
end = time.process_time() + startup
while time.process_time() < end:
    pass
print(json.dumps({'protocol_version': 1, 'type': 'ready'}), flush=True)
if mode == 'exit_after_ready':
    time.sleep(0.03)
    sys.exit(7)
rng = random.Random(1)
for line in sys.stdin:
    state = json.loads(line)
    if mode == 'oom':
        exhaust_memory()
    if mode == 'memory_error':
        raise MemoryError('probe text alone is not proof of sandbox OOM')
    if mode == 'crash':
        raise RuntimeError('probe crash')
    if mode in ('exit_zero', 'exit_error'):
        sys.exit(0 if mode == 'exit_zero' else 7)
    if mode == 'signal':
        os.kill(os.getpid(), signal.SIGTERM)
    if mode == 'sigkill':
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == 'close_stdout':
        os.close(sys.stdout.fileno())
        time.sleep(60)
    if mode == 'burn':
        while True:
            pass
    if mode == 'slow':
        time.sleep(0.5)
    if mode == 'sleep_forever':
        time.sleep(60)
    if mode in ('illegal', 'illegal_burn'):
        if mode == 'illegal':
            time.sleep(0.03)
        else:
            end = time.process_time() + 0.04
            while time.process_time() < end:
                pass
        own = next(pos for pos, piece in state['board'].items() if piece['side'] == state['you'])
        print(json.dumps({'from': own, 'to': own}), flush=True)
        continue
    if mode.startswith('format_'):
        outputs = {
            'format_json': '{oops',
            'format_missing': json.dumps({'from': 'a1'}),
            'format_extra': json.dumps({'from': 'a1', 'to': 'a2', 'debug': True}),
            'format_type': json.dumps({'from': 1, 'to': 'a2'}),
            'format_coordinate': json.dumps({'from': 'a1', 'to': 'j9'}),
            'format_list': '[]',
            'format_long': 'x' * 65537,
        }
        if mode == 'format_utf8':
            sys.stdout.buffer.write(bytes([255, 10]))
            sys.stdout.buffer.flush()
        elif mode == 'format_partial':
            sys.stdout.write(json.dumps({'from': 'a1', 'to': 'a2'}))
            sys.stdout.flush()
            break
        else:
            print(outputs[mode], flush=True)
        continue
    print(json.dumps(choose_move(state, 'random', rng)), flush=True)
    if mode == 'crash_after_move':
        time.sleep(0.05)
        raise RuntimeError('probe off-turn crash')
    if mode == 'oom_after_move':
        time.sleep(0.05)
        exhaust_memory()
    if mode == 'ponder':
        end = time.process_time() + 0.25
        while time.process_time() < end:
            pass
"""


class ProbeSession(IsolatedSampleSession):
    def __init__(self, strategy, box_id, seed, mode, startup=0):
        super().__init__(strategy, box_id, seed)
        self.mode, self.startup = mode, startup

    def _start_process(self):
        command = self.base + [f"--time={self.whole_cpu_limit}", f"--wall-time={self.whole_wall_limit}",
                               f"--cg-mem={MEMORY_LIMIT_MIB * 1024}", f"--meta={self._meta}", "--run", "--",
                               "/usr/bin/python3", "-I", "-B", "-u", "-c", PROBE,
                               self.mode, str(self.startup)]
        return subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=ROOT)


def blocked_game(side, first=None):
    original = sample_game(first or side)
    barrier = ({"a2", "b2", "c2", "d1", "d2"} if side == "A"
               else {"f8", "f9", "g8", "h8", "i8"})
    return Game(original.board, original.goals, original.bombs, original.obstacles | barrier,
                turn=first or side)


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/local/bin/isolate").is_file(),
                     "requires the configured Ubuntu Isolate environment")
class CpuTrialTests(unittest.TestCase):
    def test_real_sandboxes_stop_at_25_moves_each_and_save_material_win(self):
        from scripts.replay_fixtures import material_fixture
        from ottv2.engine import sample_game as nine_piece_game
        from ottv2.file_session import IsolatedFileSession
        fixture=material_fixture('A',limit=50)
        with TemporaryDirectory() as temporary:
            directory=Path(temporary)
            scripts={}
            for side in ('A','B'):
                moves=[{k:event[k] for k in ('from','to')} for event in fixture['history'] if event['side']==side]
                (directory/side).mkdir()
                path=directory/side/'main.py'
                path.write_text('import json,sys\nmoves='+repr(moves)+
                    '\nprint(json.dumps({"protocol_version":1,"type":"ready"}),flush=True)\n'
                    'for line in sys.stdin:\n print(json.dumps(moves.pop(0)),flush=True)\n')
                scripts[side]=path
            target=directory/'capped.json'
            with patch('ottv2.cpu_trial.sample_game',side_effect=nine_piece_game):
                game,_,_=current_trial(seed=1,result_file=target,emit=lambda _:None,
                    session_factory=lambda strategy,box,seed:IsolatedFileSession(scripts['A' if box==0 else 'B'],box,seed))
            self.assertEqual((game.result,game.reason,len(game.history)),('A','material_advantage',50))
            record=json.loads(target.read_bytes())
            self.assertEqual(record['limits']['max_valid_moves'],50)
            self.assertEqual(len(record['clock_history']),51)
            self.assertTrue(record['cleanup_complete'])
        self.assert_clean()

    def test_current_policy_is_recorded_and_extended_safeguard_cleans_up(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'new-policy.json'
            sessions=[]
            def factory(strategy, box, seed):
                s=IsolatedSampleSession(strategy,box,seed)
                sessions.append(s)
                return s
            game,_,_=current_trial(result_file=target,session_factory=factory,emit=lambda _:None)
            record=json.loads(target.read_text())
            self.assertIsNotNone(game.result)
            self.assertEqual(record['limits'],{'cpu_budget_seconds':7.0,'response_limit_seconds':2.5,'memory_limit_mib_per_bot':64,'max_valid_moves':50,'move_limit_tiebreak':'piece_count'})
            self.assertEqual(record['clock_history'][0],{'A':7.0,'B':7.0})
            self.assertTrue(all(s.whole_wall_limit==140 and s.whole_cpu_limit==145 for s in sessions))
            self.assertTrue(record['cleanup_complete'])
        self.assert_clean()

    def setUp(self):
        # Keep existing fault/adjudication scenarios on their original explicit map.
        mapping = patch("ottv2.cpu_trial.sample_game", side_effect=sample_game)
        mapping.start()
        self.addCleanup(mapping.stop)

    def test_terminal_move_is_not_overwritten_by_a_late_clock_sample(self):
        def expire(guard):
            side=guard.active_side
            guard.used[side]=guard.seconds
            guard.stop_bots()
            raise CpuBudgetExceeded(side,guard.seconds,guard.seconds)
        with TemporaryDirectory() as temporary:
            target=Path(temporary)/'terminal.json'
            with patch('ottv2.cpu_trial.CpuBudgetGuard.finish_turn',expire):
                game,_,_=current_trial(max_valid_moves=1,result_file=target,emit=lambda _:None)
            record=json.loads(target.read_bytes())
            self.assertEqual((game.result,game.reason),('draw','move_limit'))
            self.assertEqual(record['history'][-1]['reason'],record['result']['reason'])
        self.assert_clean()

    def test_clock_trace_keeps_an_accepted_move_if_final_check_expires_cpu(self):
        def expire(guard):
            side = guard.active_side
            guard.used[side] = guard.seconds
            guard.stop_bots()
            raise CpuBudgetExceeded(side, guard.seconds, guard.seconds)

        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "match.json"
            with patch("ottv2.cpu_trial.CpuBudgetGuard.finish_turn", expire):
                game, _, _ = run_trial(result_file=target, emit=lambda _: None)
            record = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual((game.result, game.reason, len(game.history)), ("B", "cpu_timeout", 1))
            self.assertEqual(len(record["clock_history"]), 2)
            self.assertEqual(record["clock_history"][0]["A"], 5.0)
            self.assertEqual(record["clock_history"][1]["A"], 0)
            self.assertEqual(record["final_clock"]["A"], 0)
        self.assert_clean()

    def test_saved_result_replays_the_same_normal_match(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "match.json"
            game, used, elapsed = run_trial(result_file=target, emit=lambda _: None)
            record = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], 1)
            self.assertEqual(record["limits"]["memory_limit_mib_per_bot"], 64)
            self.assertEqual((record["result"]["winner"], record["result"]["reason"],
                              record["result"]["valid_move_count"]), ("B", "missing_kind", 24))
            self.assertEqual(record["history"], game.history)
            self.assertEqual(record["final_state"], state_message(game))
            self.assertEqual(record["elapsed_seconds_including_cleanup"], elapsed)
            self.assertEqual(record["illegal_attempts"], {"A": 0, "B": 0})
            self.assertEqual(len(record["clock_history"]), len(game.history) + 1)
            self.assertEqual(record["clock_history"][0], {"A": 5.0, "B": 5.0})
            for index, move in enumerate(game.history):
                before, after = record["clock_history"][index:index+2]
                opponent = "B" if move["side"] == "A" else "A"
                self.assertEqual(after[opponent], before[opponent])
                self.assertLessEqual(after[move["side"]], before[move["side"]])
            for side in ("A", "B"):
                self.assertEqual(record["final_clock"][side], max(0, 5.0 - used[side]))
            self.assertEqual(record["clock_history"][-1], record["final_clock"])
            self.assertTrue(record["cleanup_complete"])
            self.assertTrue(record["created_at_utc"].endswith("+00:00"))
            state = record["initial_state"]
            replay = Game({pos: Piece(**piece) for pos, piece in state["board"].items()},
                          state["goals"], set(state["bombs"]), set(state["obstacles"]), state["turn"])
            for move in record["history"]:
                self.assertEqual(replay.play({"from": move["from"], "to": move["to"]}), move)
            self.assertEqual(state_message(replay), record["final_state"])
            self.assertEqual((replay.result, replay.reason), (game.result, game.reason))
            for side in ("A", "B"):
                self.assertEqual(record["resources"][side]["cpu_charged_seconds"], used[side])
                self.assertIn("cg-mem", record["resources"][side]["isolate_metadata"])
            for name, digest in record["source_sha256"].items():
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)
            self.assertEqual(list(Path(temporary).iterdir()), [target])
        self.assert_clean()

    def test_saved_bot_losses_have_reason_details_and_no_invented_moves(self):
        for mode, reason in (("format_missing", "format_error"), ("crash", "runtime_error"),
                             ("oom", "memory_limit"), ("startup_schema", "startup_format_error"),
                             ("sleep_forever", "response_timeout"), ("burn", "cpu_timeout")):
            with self.subTest(mode=mode), TemporaryDirectory() as temporary:
                target = Path(temporary) / "match.json"

                def factory(strategy, box_id, seed):
                    return (ProbeSession(strategy, box_id, seed, mode) if box_id == 0
                            else IsolatedSampleSession(strategy, box_id, seed))

                game, _, _ = run_trial(result_file=target, session_factory=factory,
                                       cpu_budget=0.12 if mode == "burn" else 1.5,
                                       response_timeout=0.2 if mode == "sleep_forever" else 1,
                                       emit=lambda _: None)
                record = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual((game.result, record["result"]["winner"], record["result"]["reason"]),
                                 ("B", "B", reason))
                self.assertEqual(record["result"]["detail"]["side"], "A")
                self.assertEqual(record["result"]["valid_move_count"], 0)
                self.assertEqual(record["history"], [])
                self.assertEqual(len(record["clock_history"]), 1)
                if mode == "burn":
                    self.assertEqual(record["final_clock"]["A"], 0)
                    self.assertEqual(record["clock_history"][0]["A"], 0.12)
                self.assertEqual(record["initial_state"], record["final_state"])
                self.assertTrue(record["cleanup_complete"])
                self.assert_clean()

    def test_system_or_double_startup_failure_does_not_save_a_match_result(self):
        for mode in ("system", "double_startup"):
            with self.subTest(mode=mode), TemporaryDirectory() as temporary:
                target = Path(temporary) / "match.json"

                def factory(strategy, box_id, seed):
                    if mode == "system":
                        raise OSError("test infrastructure failure")
                    return ProbeSession(strategy, box_id, seed, "startup_schema")

                with self.assertRaises((OSError, RuntimeError)):
                    run_trial(result_file=target, session_factory=factory, emit=lambda _: None)
                self.assertFalse(target.exists())
                self.assert_clean()

    def test_save_failure_is_reported_after_both_bots_have_been_cleaned_up(self):
        sessions, messages = [], []

        def factory(strategy, box_id, seed):
            session = IsolatedSampleSession(strategy, box_id, seed)
            sessions.append(session)
            return session

        with TemporaryDirectory() as temporary:
            with patch("ottv2.cpu_trial.write_result", side_effect=OSError("test result write failure")):
                with self.assertRaisesRegex(OSError, "result write failure"):
                    run_trial(result_file=Path(temporary) / "match.json",
                              session_factory=factory, emit=messages.append)
            self.assertFalse(any(line.startswith("RESULT SAVED:") for line in messages))
            self.assertTrue(all(session.process.poll() is not None for session in sessions))
            self.assert_clean()

    def test_existing_result_file_is_rejected_before_starting_any_bot(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "match.json"
            target.write_text("existing result", encoding="utf-8")
            with patch("ottv2.cpu_trial.IsolatedSampleSession") as factory:
                with self.assertRaises(FileExistsError):
                    run_trial(result_file=target, emit=lambda _: None)
                factory.assert_not_called()
            self.assertEqual(target.read_text(), "existing result")
        self.assert_clean()

    def test_all_bundled_bots_with_no_legal_move_retry_until_original_deadline(self):
        for side in ("A", "B"):
            for strategy in ("random", "greedy", "search", "retry-demo"):
                with self.subTest(side=side, strategy=strategy):
                    game = blocked_game(side)
                    self.assertEqual(game.legal_moves(), [])
                    before = (game.position_key(), list(game.history), dict(game._seen))
                    sent, messages, sessions = [], [], []

                    class RecordingSession(IsolatedSampleSession):
                        def send_state(self, current, time_info=None):
                            sent.append((current.turn, current.position_key(),
                                         len(current.history), dict(current._seen), time_info))
                            super().send_state(current, time_info)

                    def factory(bot, box_id, seed):
                        result = RecordingSession(bot, box_id, seed)
                        sessions.append(result)
                        return result

                    with patch("ottv2.cpu_trial.sample_game", return_value=game):
                        result, used, elapsed = run_trial(bot_a=strategy, bot_b=strategy,
                            session_factory=factory, response_timeout=0.2, emit=messages.append)
                    self.assertIs(result, game)
                    self.assertEqual((game.result, game.reason),
                                     ("B" if side == "A" else "A", "response_timeout"))
                    self.assertEqual((game.position_key(), game.history, dict(game._seen)), before)
                    self.assertGreaterEqual(len(sent), 2)
                    self.assertTrue(all(item[:4] == sent[0][:4] for item in sent))
                    self.assertTrue(all(item[0] == side for item in sent))
                    self.assertLess(sent[-1][4]["response_remaining_seconds"],
                                    sent[0][4]["response_remaining_seconds"])
                    remaining = [item[4]["cpu_remaining_seconds"][side] for item in sent]
                    self.assertEqual(remaining, sorted(remaining, reverse=True))
                    self.assertEqual(used["B" if side == "A" else "A"], 0)
                    self.assertEqual(sum(line.startswith("NO LEGAL MOVES") for line in messages), 1)
                    self.assertFalse(any(line.startswith(("001 ", "CLOCK:", "TRIAL STOP:")) for line in messages))
                    self.assertLessEqual(sum(line.startswith("ILLEGAL ") for line in messages), 4)
                    self.assertTrue(all(session.process.poll() is not None for session in sessions))
                    self.assertLess(elapsed, 5)
                    self.assert_clean()

    def test_blocked_bot_can_expire_cpu_instead_of_response_deadline(self):
        game = blocked_game("A")

        def factory(strategy, box_id, seed):
            return (ProbeSession(strategy, box_id, seed, "illegal_burn") if box_id == 0
                    else IsolatedSampleSession(strategy, box_id, seed))

        with patch("ottv2.cpu_trial.sample_game", return_value=game):
            result, used, _ = run_trial(session_factory=factory, cpu_budget=0.12, emit=lambda _: None)
        self.assertEqual((result.result, result.reason, len(result.history)), ("B", "cpu_timeout", 0))
        self.assertGreaterEqual(used["A"], 0.12)
        self.assertEqual(sum(result._seen.values()), 1)
        self.assert_clean()

    def test_blocked_bot_that_does_not_reply_loses_at_response_deadline(self):
        game = blocked_game("A")
        before = game.position_key()

        def factory(strategy, box_id, seed):
            return (ProbeSession(strategy, box_id, seed, "sleep_forever") if box_id == 0
                    else IsolatedSampleSession(strategy, box_id, seed))

        with patch("ottv2.cpu_trial.sample_game", return_value=game):
            result, used, elapsed = run_trial(session_factory=factory, response_timeout=0.15, emit=lambda _: None)
        self.assertEqual((result.result, result.reason, len(result.history)), ("B", "response_timeout", 0))
        self.assertEqual(result.position_key(), before)
        self.assertLess(used["A"], 1.5)
        self.assertLess(elapsed, 5)
        self.assert_clean()

    def test_no_legal_move_does_not_excuse_a_malformed_response(self):
        game = blocked_game("A")

        def factory(strategy, box_id, seed):
            return (ProbeSession(strategy, box_id, seed, "format_missing") if box_id == 0
                    else IsolatedSampleSession(strategy, box_id, seed))

        with patch("ottv2.cpu_trial.sample_game", return_value=game):
            result, _, _ = run_trial(session_factory=factory, emit=lambda _: None)
        self.assertEqual((result.result, result.reason, len(result.history)), ("B", "format_error", 0))
        self.assert_clean()

    def test_blocked_turn_after_accepted_move_preserves_history_and_opponents_clock(self):
        game = blocked_game("B", first="A")
        self.assertTrue(game.legal_moves())
        messages = []
        with patch("ottv2.cpu_trial.sample_game", return_value=game):
            result, used, _ = run_trial(response_timeout=0.2, emit=messages.append)
        self.assertEqual((result.result, result.reason, len(result.history)), ("A", "response_timeout", 1))
        self.assertEqual(result.turn, "B")
        self.assertEqual(sum(result._seen.values()), 2)
        self.assertTrue(any(line.startswith("001 A:") for line in messages))
        self.assertFalse(any(line.startswith("002 B:") for line in messages))
        self.assertTrue(any(line.startswith("NO LEGAL MOVES B:") for line in messages))
        self.assertGreater(used["A"], 0)
        self.assertLess(used["A"], 1.5)
        self.assert_clean()

    def test_real_malformed_ready_from_either_bot_is_a_startup_loss(self):
        for box_id in (0, 1):
            for mode in ("startup_json", "startup_schema"):
                with self.subTest(box=box_id, mode=mode):
                    self.assert_startup_loss(mode, box_id, "startup_format_error")

    def test_real_missing_ready_uses_startup_deadline_and_does_not_charge_match_cpu(self):
        self.assert_startup_loss("startup_sleep", 1, "startup_timeout")

    def test_real_startup_python_crash_is_classified(self):
        self.assert_startup_loss("startup_crash", 0, "startup_runtime_error")

    def assert_startup_loss(self, mode, box_id, reason):
        sessions, messages = [], []

        def factory(strategy, current_box, seed):
            result = (ProbeSession(strategy, current_box, seed, mode) if current_box == box_id
                      else IsolatedSampleSession(strategy, current_box, seed))
            if mode == "startup_sleep":
                result.startup_timeout = 0.2  # Same path as the 5s default, tested quickly.
            sessions.append(result)
            return result

        game, used, elapsed = run_trial(session_factory=factory, emit=messages.append)
        side = "A" if box_id == 0 else "B"
        self.assertEqual((game.result, game.reason, len(game.history)),
                         ("B" if box_id == 0 else "A", reason, 0))
        self.assertEqual(used, {"A": 0, "B": 0})
        self.assertEqual(sum(game._seen.values()), 1)
        self.assertTrue(any(line.startswith(f"STARTUP ERROR {side}: {reason}") for line in messages))
        self.assertFalse(any(line.startswith(("001 ", "CLOCK:", "SENT TIME")) for line in messages))
        self.assertTrue(all(session.process.poll() is not None for session in sessions))
        self.assertTrue(all(session.process.stdout.closed for session in sessions))
        self.assertLess(elapsed, 5)
        self.assert_clean()

    def test_both_startup_failures_get_no_invented_winner(self):
        messages = []

        def factory(strategy, box_id, seed):
            return ProbeSession(strategy, box_id, seed, "startup_json")

        with self.assertRaisesRegex(RuntimeError, "both bots failed startup"):
            run_trial(session_factory=factory, emit=messages.append)
        self.assertFalse(any(line.startswith("RESULT:") for line in messages))
        self.assert_clean()

    def test_ready_bot_that_dies_waiting_cannot_win_over_another_startup_failure(self):
        messages = []

        def factory(strategy, box_id, seed):
            result = ProbeSession(strategy, box_id, seed, "exit_after_ready" if box_id == 0
                                  else "startup_sleep")
            result.startup_timeout = 0.2
            return result

        with self.assertRaisesRegex(RuntimeError, "both bots failed startup"):
            run_trial(session_factory=factory, emit=messages.append)
        self.assertFalse(any(line.startswith("RESULT:") for line in messages))
        self.assert_clean()

    def test_second_sandbox_infrastructure_failure_has_no_winner(self):
        sessions, messages = [], []

        def factory(strategy, box_id, seed):
            if box_id == 1:
                raise OSError("test sandbox setup failure")
            result = IsolatedSampleSession(strategy, box_id, seed)
            sessions.append(result)
            return result

        with self.assertRaisesRegex(OSError, "sandbox setup failure"):
            run_trial(session_factory=factory, emit=messages.append)
        self.assertFalse(any(line.startswith(("RESULT:", "STARTUP ERROR")) for line in messages))
        self.assertIsNotNone(sessions[0].process.poll())
        self.assert_clean()

    def test_isolate_internal_error_during_startup_is_not_a_bot_loss(self):
        messages = []

        class InternalFailure(ProbeSession):
            def _collect_outcome(self):
                super()._collect_outcome()
                self.metadata = {"status": "XX", "message": "test internal Isolate error"}

        def factory(strategy, box_id, seed):
            return (InternalFailure(strategy, box_id, seed, "startup_exit") if box_id == 0
                    else IsolatedSampleSession(strategy, box_id, seed))

        with self.assertRaisesRegex(RuntimeError, "cannot classify bot runtime failure"):
            run_trial(session_factory=factory, emit=messages.append)
        self.assertFalse(any(line.startswith(("RESULT:", "STARTUP ERROR")) for line in messages))
        self.assert_clean()

    def test_bot_exceeding_memory_loses_before_any_accepted_move(self):
        self.assert_memory_loss(box_id=0, winner="B", moves=0)

    def test_memory_failure_by_b_keeps_the_accepted_a_move(self):
        self.assert_memory_loss(box_id=1, winner="A", moves=1)

    def assert_memory_loss(self, box_id, winner, moves):
        sessions, positions, messages = [], [], []

        class RecordingProbe(ProbeSession):
            def send_state(self, game, time_info=None):
                positions.append((game.position_key(), len(game.history), dict(game._seen)))
                super().send_state(game, time_info)

        def factory(strategy, current_box, seed):
            result = (RecordingProbe(strategy, current_box, seed, "oom") if current_box == box_id
                      else IsolatedSampleSession(strategy, current_box, seed))
            sessions.append(result)
            return result

        game, _, elapsed = run_trial(session_factory=factory, emit=messages.append)
        side = "A" if box_id == 0 else "B"
        self.assertEqual((game.result, game.reason, len(game.history)), (winner, "memory_limit", moves))
        self.assertEqual(positions, [(game.position_key(), moves, dict(game._seen))])
        loser = sessions[box_id]
        self.assertEqual(loser.metadata["cg-oom-killed"], "1")
        self.assertGreater(loser.memory_events["oom"], 0)
        self.assertGreater(loser.memory_events["max"], 0)
        self.assertTrue(any(line.startswith(f"MEMORY LIMIT {side}: limit=64 MiB") for line in messages))
        self.assertFalse(any(line.startswith(("RUNTIME ERROR", "ILLEGAL ")) for line in messages))
        self.assertTrue(all(session.process.poll() is not None for session in sessions))
        self.assertTrue(all(session.process.stdout.closed for session in sessions))
        self.assertLess(elapsed, 5)
        self.assert_clean()

    def test_memory_limit_applies_while_pondering_outside_own_turn(self):
        sessions, messages = [], []

        def factory(strategy, box_id, seed):
            result = ProbeSession(strategy, box_id, seed, "oom_after_move" if box_id == 0 else "slow")
            sessions.append(result)
            return result

        game, _, _ = run_trial(session_factory=factory, emit=messages.append)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "memory_limit", 1))
        self.assertEqual(game.turn, "B")
        self.assertTrue(any(line.startswith("MEMORY LIMIT A:") for line in messages))
        self.assertGreater(sessions[0].memory_events["oom_kill"], 0)
        self.assertTrue(all(session.process.poll() is not None for session in sessions))
        self.assert_clean()

    def test_oom_before_ready_is_classified_without_a_move_or_cpu_charge(self):
        def factory(strategy, box_id, seed):
            return (ProbeSession(strategy, box_id, seed, "startup_oom") if box_id == 0
                    else IsolatedSampleSession(strategy, box_id, seed))

        messages = []
        game, used, _ = run_trial(session_factory=factory, emit=messages.append)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "startup_memory_limit", 0))
        self.assertEqual(used, {"A": 0, "B": 0})
        self.assertTrue(any(line.startswith("STARTUP ERROR A: startup_memory_limit") for line in messages))
        self.assert_clean()

    def test_runtime_failures_stop_the_match_without_retry(self):
        for mode in ("crash", "exit_zero", "exit_error", "signal", "sigkill", "memory_error"):
            with self.subTest(mode=mode):
                self.assert_runtime_loss(mode, box_id=0, winner="B", moves=0)

    def test_bot_that_closes_stdout_but_keeps_running_still_hits_response_deadline(self):
        def factory(strategy, box_id, seed):
            return (ProbeSession(strategy, box_id, seed, "close_stdout") if box_id == 0
                    else IsolatedSampleSession(strategy, box_id, seed))

        # Isolate's monitor can keep the output pipe open after the child closes it.
        # Without observable EOF, the existing wall deadline still stops the bot.
        game, _, elapsed = run_trial(session_factory=factory, response_timeout=0.2, emit=lambda _: None)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "response_timeout", 0))
        self.assertLess(elapsed, 5)
        self.assert_clean()

    def test_runtime_error_by_b_preserves_the_accepted_a_move(self):
        self.assert_runtime_loss("crash", box_id=1, winner="A", moves=1)

    def assert_runtime_loss(self, mode, box_id, winner, moves):
        sessions, positions, messages = [], [], []

        class RecordingProbe(ProbeSession):
            def send_state(self, game, time_info=None):
                positions.append((game.position_key(), len(game.history), dict(game._seen)))
                super().send_state(game, time_info)

        def factory(strategy, current_box, seed):
            result = (RecordingProbe(strategy, current_box, seed, mode) if current_box == box_id
                      else IsolatedSampleSession(strategy, current_box, seed))
            sessions.append(result)
            return result

        game, _, elapsed = run_trial(session_factory=factory, emit=messages.append)
        side = "A" if box_id == 0 else "B"
        self.assertEqual((game.result, game.reason, len(game.history)), (winner, "runtime_error", moves))
        self.assertEqual(positions, [(game.position_key(), moves, dict(game._seen))])
        line = next(line for line in messages if line.startswith(f"RUNTIME ERROR {side}:"))
        if mode == "crash":
            self.assertIn("RuntimeError: probe crash", line)
            self.assertIn("exitcode=1", line)
        self.assertFalse(any(line.startswith("ILLEGAL ") for line in messages))
        self.assertTrue(all(session.process.poll() is not None for session in sessions))
        self.assertTrue(all(session.process.stdout.closed for session in sessions))
        self.assertLess(elapsed, 5)
        self.assert_clean()

    def test_off_turn_crash_is_seen_while_waiting_for_opponent(self):
        sessions, messages = [], []

        def factory(strategy, box_id, seed):
            result = ProbeSession(strategy, box_id, seed, "crash_after_move" if box_id == 0 else "slow")
            sessions.append(result)
            return result

        game, _, _ = run_trial(session_factory=factory, emit=messages.append)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "runtime_error", 1))
        self.assertEqual(game.turn, "B")
        self.assertTrue(any("probe off-turn crash" in line for line in messages))
        self.assertFalse(any(line.startswith("002 B:") for line in messages))
        self.assertTrue(all(session.process.poll() is not None for session in sessions))
        self.assert_clean()

    def test_startup_exit_is_a_bot_loss_after_confirming_the_opponent(self):
        def factory(strategy, box_id, seed):
            return (ProbeSession(strategy, box_id, seed, "startup_exit") if box_id == 0
                    else IsolatedSampleSession(strategy, box_id, seed))

        messages = []
        game, used, _ = run_trial(session_factory=factory, emit=messages.append)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "startup_runtime_error", 0))
        self.assertEqual(used, {"A": 0, "B": 0})
        self.assertTrue(any(line.startswith("READY B:") for line in messages))
        self.assert_clean()

    def test_illegal_attempt_keeps_board_turn_history_and_cpu_before_recovery(self):
        sent = []
        messages = []

        class RecordingSession(IsolatedSampleSession):
            def send_state(self, game, time_info=None):
                if self.box_id == 0:
                    sent.append((game.position_key(), len(game.history), dict(game._seen), time_info))
                super().send_state(game, time_info)

        game, used, _ = run_trial(bot_a="retry-demo", session_factory=RecordingSession,
                                  emit=messages.append, show_time=True)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "missing_kind", 24))
        self.assertEqual(sent[0][:3], sent[1][:3])
        self.assertLessEqual(sent[1][3]["cpu_remaining_seconds"]["A"],
                             sent[0][3]["cpu_remaining_seconds"]["A"])
        self.assertLess(sent[1][3]["response_remaining_seconds"],
                        sent[0][3]["response_remaining_seconds"])
        self.assertEqual(sum(line.startswith("ILLEGAL A:") for line in messages), 1)
        self.assertTrue(any(line.startswith("001 A:") for line in messages))
        self.assertGreater(used["A"], 0)
        self.assert_clean()

    def test_repeated_illegal_moves_expire_original_response_deadline(self):
        messages = []

        def factory(strategy, box_id, seed):
            if box_id == 0:
                return ProbeSession(strategy, box_id, seed, "illegal")
            return IsolatedSampleSession(strategy, box_id, seed)

        game, used, elapsed = run_trial(session_factory=factory, response_timeout=0.2,
                                        emit=messages.append)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "response_timeout", 0))
        invalid = [line for line in messages if line.startswith("ILLEGAL A:")]
        self.assertGreaterEqual(len(invalid), 2)
        self.assertLessEqual(len(invalid), 4)
        self.assertLess(used["A"], 1.5)
        self.assertLess(elapsed, 5)
        self.assert_clean()

    def test_cpu_spent_on_illegal_attempts_is_not_refunded(self):
        def factory(strategy, box_id, seed):
            if box_id == 0:
                return ProbeSession(strategy, box_id, seed, "illegal_burn")
            return IsolatedSampleSession(strategy, box_id, seed)

        game, used, _ = run_trial(cpu_budget=0.12, session_factory=factory, emit=lambda _: None)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "cpu_timeout", 0))
        self.assertGreaterEqual(used["A"], 0.12)
        self.assert_clean()

    def test_format_errors_are_not_retried_as_illegal_moves(self):
        for mode in ("format_json", "format_missing", "format_extra", "format_type",
                     "format_coordinate", "format_list", "format_long", "format_utf8",
                     "format_partial"):
            with self.subTest(mode=mode):
                self.assert_format_loss(mode, box_id=0, winner="B", moves=0)

    def test_format_error_by_b_preserves_the_accepted_a_move(self):
        self.assert_format_loss("format_missing", box_id=1, winner="A", moves=1)

    def assert_format_loss(self, mode, box_id, winner, moves):
        messages, sessions, positions = [], [], []

        class RecordingProbe(ProbeSession):
            def send_state(self, game, time_info=None):
                positions.append((game.position_key(), len(game.history), dict(game._seen)))
                super().send_state(game, time_info)

        def factory(strategy, current_box, seed):
            result = (RecordingProbe(strategy, current_box, seed, mode) if current_box == box_id
                      else IsolatedSampleSession(strategy, current_box, seed))
            sessions.append(result)
            return result

        game, used, _ = run_trial(session_factory=factory, emit=messages.append)
        side = "A" if box_id == 0 else "B"
        self.assertEqual((game.result, game.reason, len(game.history)), (winner, "format_error", moves))
        self.assertEqual(positions, [(game.position_key(), moves, dict(game._seen))])
        self.assertTrue(any(line.startswith(f"FORMAT ERROR {side}:") for line in messages))
        self.assertFalse(any(line.startswith("ILLEGAL ") for line in messages))
        self.assertTrue(all(value < 1.5 for value in used.values()))
        self.assertTrue(all(session.process.poll() is not None for session in sessions))
        self.assertTrue(all(session.process.stdout.closed for session in sessions))
        self.assert_clean()

    def test_output_io_failure_remains_a_system_error(self):
        messages = []

        class ReadFailure(IsolatedSampleSession):
            def decode_reply(self, reply):
                if self.ready:
                    raise OSError("test host pipe failure")
                return super().decode_reply(reply)

        with self.assertRaisesRegex(OSError, "host pipe failure"):
            run_trial(session_factory=ReadFailure, emit=messages.append)
        self.assertFalse(any(line.startswith(("RESULT:", "FORMAT ERROR")) for line in messages))
        self.assert_clean()

    def test_startup_format_failure_has_a_separate_reason(self):
        messages = []

        class StartupFailure(IsolatedSampleSession):
            def decode_reply(self, reply):
                raise FormatError("test malformed startup response")

        def factory(strategy, box_id, seed):
            return (StartupFailure(strategy, box_id, seed) if box_id == 0
                    else IsolatedSampleSession(strategy, box_id, seed))

        game, used, _ = run_trial(session_factory=factory, emit=messages.append)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "startup_format_error", 0))
        self.assertEqual(used, {"A": 0, "B": 0})
        self.assertFalse(any(line.startswith("FORMAT ERROR") for line in messages))
        self.assert_clean()

    def test_each_sandbox_receives_clock_snapshots_with_its_state(self):
        sent = {"A": [], "B": []}
        messages = []

        class RecordingSession(IsolatedSampleSession):
            def send_state(self, game, time_info=None):
                self_side = "A" if self.box_id == 0 else "B"
                if not sent[self_side]:
                    actual_limit = int(self.cpu_meter.stat_file.with_name("memory.max").read_text())
                    if actual_limit != MEMORY_LIMIT_MIB * 1024 * 1024:
                        raise RuntimeError(f"unexpected live cgroup memory limit: {actual_limit}")
                sent[self_side].append(time_info)
                super().send_state(game, time_info)

        game, _, _ = run_trial(session_factory=RecordingSession, show_time=True, emit=messages.append)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "missing_kind", 24))
        for side in ("A", "B"):
            self.assertEqual(len(sent[side]), 12)
            own = [timing["cpu_remaining_seconds"][side] for timing in sent[side]]
            self.assertEqual(own, sorted(own, reverse=True))
            self.assertGreater(own[0], own[-1])
            for timing in sent[side]:
                self.assertEqual(timing["cpu_budget_seconds"], 5.0)
                self.assertEqual(timing["response_limit_seconds"], 1)
                self.assertTrue(0 < timing["response_remaining_seconds"] <= 1)
                self.assertTrue(all(0 < value <= 5.0 for value in timing["cpu_remaining_seconds"].values()))
            traced = [json.loads(line.split(": ", 1)[1]) for line in messages
                      if line.startswith(f"SENT TIME {side}: ")]
            self.assertEqual(traced, sent[side])
        self.assert_clean()

    def assert_clean(self):
        self.assertFalse((BOX_ROOT / "0").exists())
        self.assertFalse((BOX_ROOT / "1").exists())

    def test_samples_complete_under_one_second_each(self):
        game, used, _ = run_trial(emit=lambda _: None)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "missing_kind", 24))
        self.assertTrue(all(value < 1 for value in used.values()))
        self.assert_clean()

    def test_burning_bot_loses_and_is_stopped(self):
        def factory(strategy, box_id, seed):
            if box_id == 0:
                return ProbeSession(strategy, box_id, seed, "burn")
            return IsolatedSampleSession(strategy, box_id, seed)

        game, used, _ = run_trial(cpu_budget=0.15, session_factory=factory, emit=lambda _: None)
        self.assertEqual((game.result, game.reason), ("B", "cpu_timeout"))
        self.assertGreaterEqual(used["A"], 0.15)
        self.assert_clean()

    def test_sleeping_bot_loses_by_response_deadline_with_cpu_left(self):
        messages = []

        def factory(strategy, box_id, seed):
            if box_id == 0:
                return ProbeSession(strategy, box_id, seed, "sleep_forever")
            return IsolatedSampleSession(strategy, box_id, seed)

        game, used, elapsed = run_trial(session_factory=factory, response_timeout=0.15,
                                        emit=messages.append)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "response_timeout", 0))
        self.assertLess(used["A"], 1)
        self.assertLess(elapsed, 5)
        self.assertTrue(any(line.startswith("RESPONSE LIMIT: A") for line in messages))
        self.assert_clean()

    def test_later_turn_gets_a_fresh_response_deadline_but_not_fresh_cpu(self):
        messages = []

        def factory(strategy, box_id, seed):
            if box_id == 0:
                result = ProbeSession(strategy, box_id, seed, "slow")
                # The guard owns the deadline rather than SampleSession's timer.
                result.response_timeout = 0.01
                return result
            return IsolatedSampleSession(strategy, box_id, seed)

        def emit(line):
            messages.append(line)
            if line.startswith("003 A:"):
                raise RuntimeError("stop after A completes two sleeping turns")

        with self.assertRaisesRegex(RuntimeError, "A completes two"):
            run_trial(session_factory=factory, response_timeout=0.75, emit=emit)
        # Two 0.5s sleeps exceed 0.75s cumulatively, but each turn has its own deadline.
        self.assertTrue(any(line.startswith("003 A:") for line in messages))
        self.assert_clean()

    def test_two_search_bots_run_with_requested_limits_and_direct_cpu_clock(self):
        messages = []
        game, used, _ = run_trial(bot_a="search", bot_b="search", show_search=True, emit=messages.append)
        self.assertIn(game.result, ("A", "B", "draw"))
        self.assertIn(game.reason, ("goal", "missing_kind", "threefold_repetition",
                                    "cpu_timeout", "response_timeout"))
        self.assertIn("5s CPU total", messages[0])
        self.assertIn("1s wall per turn", messages[1])
        self.assertTrue(all(value >= 0 for value in used.values()))
        clocks = [line for line in messages if line.startswith("CLOCK:")]
        self.assertTrue(clocks)
        self.assertTrue(all(0 <= float(line.split("A=")[1].split("s")[0]) <= 5.0 for line in clocks))
        self.assertTrue(any(line.startswith("RESPONSE A: max_wall=") for line in messages))
        for side in ("A", "B"):
            line = next(line for line in messages if line.startswith(f"SEARCH {side} (last): "))
            stats = json.loads(line.split(": ", 1)[1])
            self.assertTrue(0 <= stats["completed_depth"] <= 4)
            self.assertTrue(0 <= stats["cpu_slice_seconds"] <= 0.08)
            self.assertTrue(0 <= stats["wall_slice_seconds"] <= 0.2)
        self.assert_clean()

    def test_pondering_exceeds_budget_without_changing_own_clock(self):
        sessions, clocks = [], []

        def factory(strategy, box_id, seed):
            result = ProbeSession(strategy, box_id, seed, "ponder" if box_id == 0 else "slow")
            sessions.append(result)
            return result

        def emit(line):
            if line.startswith("CLOCK:"):
                clocks.append(line.split(" | ")[0])  # A's charged display clock only.
                if len(clocks) == 2:
                    self.assertGreater(sessions[0].sample_cpu_seconds(), 0.2)
                    raise RuntimeError("stop after two moves")

        with self.assertRaisesRegex(RuntimeError, "stop after two moves"):
            run_trial(cpu_budget=0.15, session_factory=factory, emit=emit)
        self.assertEqual(len(clocks), 2)
        self.assertEqual(clocks[0], clocks[1])
        self.assert_clean()

    def test_startup_cpu_is_not_charged_to_the_trial_budget(self):
        sessions = []

        def factory(strategy, box_id, seed):
            result = (ProbeSession(strategy, box_id, seed, "burn", startup=0.25) if box_id == 0
                      else IsolatedSampleSession(strategy, box_id, seed))
            sessions.append(result)
            return result

        game, used, _ = run_trial(cpu_budget=0.1, session_factory=factory, emit=lambda _: None)
        self.assertEqual(game.reason, "cpu_timeout")
        self.assertGreaterEqual(sessions[0].cpu_meter.baseline_usec, 250000)
        self.assertGreaterEqual(used["A"], 0.1)
        self.assertGreater(float(sessions[0].metadata["time"]), used["A"])
        self.assert_clean()
