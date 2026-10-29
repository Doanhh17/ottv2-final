"""Run bots with own-turn CPU budgets and a wall deadline for each turn."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
from time import monotonic
from uuid import uuid4

from .clock import display_seconds_left
from .cpu_budget import DEFAULT_CPU_BUDGET_SECONDS, CpuBudgetExceeded, CpuBudgetGuard, ResponseTimeout
from .rules import MAX_VALID_MOVES, RESPONSE_LIMIT_SECONDS, validate_move_limit, whole_wall_limit
from .engine import FormatError, IllegalMove, sample_game
from .isolated_session import BOX_ROOT, MEMORY_LIMIT_MIB, IsolatedSampleSession, validate_box_pair
from .protocol import state_message
from .result_log import check_result_path, write_result
from .match_archive import validate_match_id
from .map_config import load_map
from .map_pool import choose_map
from .file_session import IsolatedFileSession
from .zip_session import IsolatedZipSession
from .bot_store import IsolatedStoredSession
from .sample_session import ROOT, BotProcessError, BotStartupError


class BotFormatLoss(RuntimeError):
    def __init__(self, side, detail):
        self.side = side
        super().__init__(f"{side}: {detail}")


class BotRuntimeLoss(RuntimeError):
    def __init__(self, side, detail):
        self.side, self.detail = side, detail
        super().__init__(f"{side}: {detail}")


class BotStartupLoss(RuntimeError):
    def __init__(self, failures):
        self.failures = failures
        super().__init__("bot startup failed")


def run_trial(seed=1, bot_a="random", bot_b="greedy", cpu_budget=DEFAULT_CPU_BUDGET_SECONDS, emit=print,
              session_factory=None, response_timeout=RESPONSE_LIMIT_SECONDS, show_time=False, show_search=False,
              result_file=None, save_result=False, map_file=None, map_pool=None,
              bot_a_file=None, bot_b_file=None, bot_a_zip=None, bot_b_zip=None,
              bot_store=None, bot_a_current=None, bot_b_current=None,
              match_id=None, map_identity=None, box_ids=(0, 1), max_valid_moves=MAX_VALID_MOVES,
              move_limit_tiebreak='piece_count'):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("run this trial inside Ubuntu with python3")
    CpuBudgetGuard({}, cpu_budget, response_timeout)  # Validate before opening any sandbox.
    validate_move_limit(max_valid_moves)
    if move_limit_tiebreak not in ('draw', 'piece_count'):
        raise ValueError('unknown move-limit tiebreak')
    box_a, box_b = validate_box_pair(box_ids)
    file_sessions = {}
    if session_factory is not None and any(p is not None for p in
                                          (bot_a_file, bot_b_file, bot_a_zip, bot_b_zip, bot_a_current, bot_b_current)):
        raise ValueError("file bots cannot be combined with session_factory")
    for paths in ((bot_a_file, bot_a_zip, bot_a_current), (bot_b_file, bot_b_zip, bot_b_current)):
        if sum(p is not None for p in paths) > 1:
            raise ValueError("choose one bot input for each side")
    if (bot_a_current is not None or bot_b_current is not None) and bot_store is None:
        raise ValueError("stored bots require bot_store")
    for side, path, box_id in (("A", bot_a_file, box_a), ("B", bot_b_file, box_b)):
        if path is not None:
            file_sessions[side] = IsolatedFileSession(path, box_id, seed)
    bot_a = "file" if bot_a_file is not None else bot_a
    bot_b = "file" if bot_b_file is not None else bot_b
    for side, path, box_id in (("A", bot_a_zip, box_a), ("B", bot_b_zip, box_b)):
        if path is not None:
            file_sessions[side] = IsolatedZipSession(path, box_id, seed)
    bot_a = "zip" if bot_a_zip is not None else bot_a
    bot_b = "zip" if bot_b_zip is not None else bot_b
    for side, bot_id, box_id in (("A", bot_a_current, box_a), ("B", bot_b_current, box_b)):
        if bot_id is not None:
            file_sessions[side] = IsolatedStoredSession(bot_store, bot_id, box_id, seed)
    bot_a = "stored" if bot_a_current is not None else bot_a
    bot_b = "stored" if bot_b_current is not None else bot_b
    if map_file is not None and map_pool is not None:
        raise ValueError("choose either map_file or map_pool")
    configured_game, map_info = (load_map(map_file, random.Random(seed).choice(("A", "B")))
                                 if map_file is not None else (None, None))
    if map_pool is not None:
        configured_game, map_info = choose_map(map_pool, random.Random(seed).choice(("A", "B")))
    match_id = uuid4().hex if match_id is None else validate_match_id(match_id)
    if map_identity is not None:
        if (map_file is None or map_pool is not None or not isinstance(map_identity, dict)
                or set(map_identity) != {'name', 'sha256'}
                or not isinstance(map_identity['name'], str)
                or not isinstance(map_identity['sha256'], str)
                or len(map_identity['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in map_identity['sha256'])):
            raise ValueError('invalid pinned map identity')
        map_info = dict(map_identity)
    result_path = (Path(result_file) if result_file is not None
                   else Path("output/matches") / f"{match_id}.json" if save_result else None)
    if result_path is not None:
        check_result_path(result_path)
    created_at = datetime.now(timezone.utc).isoformat()
    source_hashes = {}
    if result_path is not None:
        for source in sorted((ROOT / "ottv2").glob("*.py")):
            source_hashes[f"ottv2/{source.name}"] = hashlib.sha256(source.read_bytes()).hexdigest()
    for box_id in (box_a, box_b):
        box = BOX_ROOT / str(box_id)
        if box.exists() or box.is_symlink():
            raise RuntimeError(f"box {box_id} already exists; refusing to reset it")
    started = monotonic()
    game = configured_game if configured_game is not None else sample_game(random.Random(seed).choice(("A", "B")))
    game.max_valid_moves = max_valid_moves
    game.move_limit_tiebreak = move_limit_tiebreak
    initial_state = state_message(game)
    illegal_totals = {"A": 0, "B": 0}
    clock_history = [{"A": cpu_budget, "B": cpu_budget}]
    sessions = {}
    report_sessions = {}
    both_ready = False
    emit(f"CPU TRIAL: {cpu_budget:g}s CPU total on own turns per bot | A={bot_a} B={bot_b}")
    emit(f"RESPONSE LIMIT: {response_timeout:g}s wall per turn | CLOCK shows CPU seconds directly")
    expired = None
    format_loss = None
    runtime_loss = None
    memory_loss = None
    startup_loss = None

    def fail_runtime(side, error):
        # stdout can close just before the Isolate monitor finishes writing meta.
        # Give a natural exit a short bounded chance before stopping both bots.
        try:
            sessions[side].process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
        guard.stop_bots()
        raise BotRuntimeLoss(side, str(error)) from error

    def check_bots(active_side):
        if not both_ready or game.result is not None:
            return
        for side, session in sessions.items():
            # For the active bot, consume queued replies/format errors before EOF.
            if side != active_side:
                try:
                    session.check_process()
                except BotProcessError as error:
                    fail_runtime(side, error)

    try:
        with ExitStack() as stack:
            guard = CpuBudgetGuard(sessions, cpu_budget, response_timeout, health_check=check_bots)
            startup_failures = {}
            for side, strategy, box_id in (("A", bot_a, box_a), ("B", bot_b, box_b)):
                session = file_sessions.get(side) or (session_factory or IsolatedSampleSession)(strategy, box_id, seed)
                # A separate whole-session safeguard bounds this trusted sample trial.
                session.whole_wall_limit = whole_wall_limit(cpu_budget, max_valid_moves, response_timeout)
                # Free pondering must not hit the former 7s whole-session CPU cap.
                # With one CPU, this ceiling lies above the wall safeguard.
                session.whole_cpu_limit = session.whole_wall_limit + 5
                report_sessions[side] = session
                try:
                    sessions[side] = stack.enter_context(session)
                except BotStartupError as error:
                    startup_failures[side] = error
                    continue
                guard.check()
                emit(f"READY {side}: {strategy} | box={box_id}")
            for side, session in sessions.items():
                try:
                    session.check_process()
                except BotProcessError as error:
                    startup_failures[side] = BotStartupError("startup_runtime_error", str(error))
            if startup_failures:
                guard.stop_bots()
                raise BotStartupLoss(startup_failures)
            both_ready = True
            guard.begin_turn(game.turn)
            for number in range(1, 1001):
                guard.check()
                side = game.turn
                if not game.legal_moves():
                    emit(f"NO LEGAL MOVES {side}: same turn; original clock continues")
                illegal_attempts = 0
                while True:
                    try:
                        move = guard.request_move(side, game)
                        guard.check()
                        game.play(move)
                    except BotProcessError as error:
                        fail_runtime(side, error)
                    except FormatError as error:
                        guard.stop_bots()
                        # Unwind the sandboxes with an exception so intentional
                        # termination is not mistaken for a normal-exit failure.
                        raise BotFormatLoss(side, str(error)) from error
                    except IllegalMove as error:
                        # No finish_turn/begin_turn here: keep this turn's CPU and deadline.
                        illegal_attempts += 1
                        illegal_totals[side] += 1
                        if illegal_attempts <= 3:
                            if show_time:
                                emit(f"SENT TIME {side}: {json.dumps(guard.sent_time_info[side], sort_keys=True)}")
                            emit(f"ILLEGAL {side}: {move['from']} -> {move['to']} | {error} | retry_same_turn")
                        elif illegal_attempts == 4:
                            emit(f"ILLEGAL {side}: further invalid attempts hidden; same clock continues")
                        continue
                    break
                try:
                    used = guard.finish_turn()
                finally:
                    # A move can already be accepted when the final check detects
                    # a timeout or the other bot's failure. Preserve its clock too.
                    clock_history.append({team: max(0.0, cpu_budget - guard.used.get(team, 0.0))
                                          for team in ("A", "B")})
                if game.result is None:
                    # Hand the clock over as soon as the engine accepts the move.
                    guard.begin_turn(game.turn)
                if show_time:
                    emit(f"SENT TIME {side}: {json.dumps(guard.sent_time_info[side], sort_keys=True)}")
                emit(f"{number:03d} {side}: {move['from']} -> {move['to']}")
                emit(f"CLOCK: A={display_seconds_left(used['A'], cpu_budget, cpu_budget):.3f}s CPU "
                     f"| B={display_seconds_left(used['B'], cpu_budget, cpu_budget):.3f}s CPU")
                if game.result is not None:
                    break
            else:
                emit("TRIAL STOP: demo move cap reached; no tournament result assigned.")
    except BotStartupLoss as error:
        failures = {side: report_sessions[side].startup_error_detail(failure)
                    for side, failure in error.failures.items()}
        if len(failures) != 1:
            raise RuntimeError(f"both bots failed startup; no result assigned: {json.dumps(failures)}") from error
        side, (reason, detail) = next(iter(failures.items()))
        startup_loss = (side, reason, detail)
        game.result = "B" if side == "A" else "A"
        game.reason = reason
    except (CpuBudgetExceeded, ResponseTimeout) as error:
        expired = error
        # The last pre-play check already accepted this move within its deadline.
        # A later bookkeeping sample must not contradict a terminal Engine event.
        if game.result is None:
            game.result = "B" if error.side == "A" else "A"
            game.reason = "cpu_timeout" if isinstance(error, CpuBudgetExceeded) else "response_timeout"
    except BotFormatLoss as error:
        format_loss = error
        game.result = "B" if error.side == "A" else "A"
        game.reason = "format_error"
    except BotRuntimeLoss as error:
        # Validate metadata only after ExitStack has waited and drained stderr.
        # If Isolate itself failed, propagate a system error with no match winner.
        memory_detail = sessions[error.side].memory_limit_detail()
        if memory_detail is not None:
            memory_loss = (error.side, memory_detail)
            game.reason = "memory_limit"
        else:
            runtime_loss = (error.side, sessions[error.side].runtime_error_detail(error.detail))
            game.reason = "runtime_error"
        game.result = "B" if error.side == "A" else "A"
    elapsed = monotonic() - started
    if expired:
        emit(f"{'CPU' if isinstance(expired, CpuBudgetExceeded) else 'RESPONSE'} LIMIT: {expired}")
    if format_loss:
        emit(f"FORMAT ERROR {format_loss}")
    if runtime_loss:
        emit(f"RUNTIME ERROR {runtime_loss[0]}: {runtime_loss[1]}")
    if memory_loss:
        emit(f"MEMORY LIMIT {memory_loss[0]}: {memory_loss[1]}")
    if startup_loss:
        emit(f"STARTUP ERROR {startup_loss[0]}: {startup_loss[1]} | {startup_loss[2]}")
    if game.result is not None:
        emit(f"RESULT: {game.result} | reason={game.reason} | moves={len(game.history)}")
    readings = {}
    for side, session in report_sessions.items():
        readings[side] = guard.used.get(side, 0.0)
        own_wall = guard.wall_used.get(side, 0.0)
        emit(f"CPU {side}: charged={readings[side]:.6f}s on own turns | "
             f"remaining={display_seconds_left(readings[side], cpu_budget, cpu_budget):.3f}s CPU")
        emit(f"WALL {side}: total={own_wall:.6f}s on own turns (measurement only)")
        emit(f"RESPONSE {side}: max_wall={guard.response_max.get(side, 0.0):.6f}s "
             f"(limit {response_timeout:g}s per turn)")
        emit(f"META CPU {side}: total_after_ready={session.cpu_after_ready_seconds:.6f}s "
             "(includes free pondering; sampled before close)")
        if show_search:
            details = [line.removeprefix("SEARCH INFO: ") for line in "".join(session._errors).splitlines()
                       if line.startswith("SEARCH INFO: ")]
            if details:
                emit(f"SEARCH {side} (last): {details[-1]}")
    emit(f"WALL: {elapsed:.3f}s including sandbox setup, startup and cleanup")
    emit(f"CLEANUP: both bots stopped; boxes {box_a} and {box_b} removed.")
    if result_path is not None and game.result is not None:
        detail = None
        if expired:
            detail = {"side": expired.side, "message": str(expired)}
        elif format_loss:
            detail = {"side": format_loss.side, "message": str(format_loss)}
        elif runtime_loss or memory_loss:
            side, message = runtime_loss or memory_loss
            detail = {"side": side, "message": message}
        elif startup_loss:
            detail = {"side": startup_loss[0], "message": startup_loss[2]}
        resources = {}
        for side, session in report_sessions.items():
            resources[side] = {
                "cpu_charged_seconds": readings[side],
                "cpu_remaining_seconds": max(0.0, cpu_budget - readings[side]),
                "own_turn_wall_seconds": guard.wall_used.get(side, 0.0),
                "max_response_wall_seconds": guard.response_max.get(side, 0.0),
                "cpu_after_ready_seconds": session.cpu_after_ready_seconds,
                "startup_wall_seconds": session.startup_wall_seconds,
                "startup_limit_seconds": session.startup_timeout,
                "isolate_metadata": dict(session.metadata),
                "memory_events": dict(session.memory_events),
                "stderr_tail": "".join(session._errors)[-2048:],
            }
        record = {
            "schema_version": 1, "match_id": match_id, "created_at_utc": created_at,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(), "seed": seed,
            "bots": {"A": bot_a, "B": bot_b}, "source_sha256": source_hashes,
            "python_version": sys.version.split()[0],
            "limits": {"cpu_budget_seconds": cpu_budget, "response_limit_seconds": response_timeout,
                       "memory_limit_mib_per_bot": MEMORY_LIMIT_MIB,
                       **({"max_valid_moves": max_valid_moves} if max_valid_moves is not None else {}),
                       **({'move_limit_tiebreak': move_limit_tiebreak}
                          if max_valid_moves is not None and move_limit_tiebreak != 'draw' else {})},
            "initial_state": initial_state, "final_state": state_message(game),
            "result": {"winner": game.result, "reason": game.reason,
                       "valid_move_count": len(game.history), "detail": detail},
            "history": game.history, "illegal_attempts": illegal_totals, "resources": resources,
            "clock_history": clock_history,
            "final_clock": {side: max(0.0, cpu_budget - readings[side]) for side in ("A", "B")},
            "elapsed_seconds_including_cleanup": elapsed, "cleanup_complete": True,
        }
        if map_info is not None:
            record["map"] = map_info
        sources = {side: session.source_info for side, session in
                   {**file_sessions, **report_sessions}.items() if hasattr(session, 'source_info')}
        if sources:
            record["bot_sources"] = sources
        saved = write_result(result_path, record)
        emit(f"RESULT SAVED: {saved}")
    return game, readings, elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    maps = parser.add_mutually_exclusive_group()
    maps.add_argument("--map", dest="map_file", type=Path, help="validate and use a local JSON map")
    maps.add_argument("--map-pool", type=Path, help="choose a map using host randomness, independent of seed")
    a = parser.add_mutually_exclusive_group()
    b = parser.add_mutually_exclusive_group()
    a.add_argument("--bot-a", choices=("random", "greedy", "search", "retry-demo", "alpha", "guardian"), default="random")
    b.add_argument("--bot-b", choices=("random", "greedy", "search", "retry-demo", "alpha", "guardian"), default="greedy")
    a.add_argument("--bot-a-file", type=Path, help="local single-file bot named main.py")
    b.add_argument("--bot-b-file", type=Path, help="local single-file bot named main.py")
    a.add_argument("--bot-a-zip", type=Path, help="local ZIP containing only one Python file main.py")
    b.add_argument("--bot-b-zip", type=Path, help="local ZIP containing only one Python file main.py")
    a.add_argument("--bot-a-current", help="bot id in the local version store")
    b.add_argument("--bot-b-current", help="bot id in the local version store")
    parser.add_argument("--bot-store", type=Path, help="local bot version store root")
    parser.add_argument("--show-time", action="store_true", help="show the time snapshot sent with each state")
    parser.add_argument("--show-search", action="store_true", help="show the last search diagnostic from each bot")
    parser.add_argument("--result-file", type=Path, help="save JSON to this new file instead of output/matches/<id>.json")
    args = parser.parse_args()
    try:
        game, _, _ = run_trial(args.seed, args.bot_a, args.bot_b,
                              show_time=args.show_time, show_search=args.show_search,
                              result_file=args.result_file, save_result=True, map_file=args.map_file,
                              map_pool=args.map_pool, bot_a_file=args.bot_a_file, bot_b_file=args.bot_b_file,
                              bot_a_zip=args.bot_a_zip, bot_b_zip=args.bot_b_zip,
                              bot_store=args.bot_store, bot_a_current=args.bot_a_current,
                              bot_b_current=args.bot_b_current)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"CPU TRIAL ERROR: {error}", file=sys.stderr)
        return 1
    return 0 if game.result is not None else 2


if __name__ == "__main__":
    sys.exit(main())
