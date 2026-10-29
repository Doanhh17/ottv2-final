"""A complete local match between bundled bots in separate Isolate boxes."""

import argparse
from contextlib import ExitStack
import random
import subprocess
import sys

from .engine import sample_game
from .clock import display_seconds_left
from .isolated_session import BOX_ROOT, IsolatedSampleSession


def run_match(seed=1, max_moves=1000, emit=print, measure_cpu=False, show_clock=False):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("run this demo inside Ubuntu with python3")
    # Check both before starting either; never reset another run's box.
    for box_id in (0, 1):
        box = BOX_ROOT / str(box_id)
        if box.exists() or box.is_symlink():
            raise RuntimeError(f"box {box_id} already exists; refusing to reset it")
    first = random.Random(seed).choice(("A", "B"))
    game = sample_game(first)
    emit(f"OTTv2 ISOLATED MATCH | seed={seed} | first={first}")
    emit("Bundled samples only | META CPU includes startup; tournament clock not implemented")
    if measure_cpu:
        emit("CPU AFTER READY: cgroup measurements enabled; not a tournament clock")
    if show_clock:
        emit("CLOCK PREVIEW BUDGET: 5.000s CPU -> 30.000s display per bot")
    with ExitStack() as stack:
        sessions = {}
        for side, strategy, box_id, cpu in (("A", "random", 0, 0), ("B", "greedy", 1, 2)):
            session = stack.enter_context(IsolatedSampleSession(strategy, box_id, seed))
            sessions[side] = session
            emit(f"READY {side}: {strategy} | box={box_id} | CPU={cpu}")
        for number in range(1, max_moves + 1):
            if not game.legal_moves():
                emit("DEMO STOP: no legal move; future Worker retries until timeout.")
                break
            side = game.turn
            move = sessions[side].request_move(game)
            event = game.play(move)
            extra = " BOMB" if event["bomb"] else ""
            if event["capture"]:
                extra += " CAPTURE=" + event["capture"]
            emit(f"{number:03d} {side} box={sessions[side].box_id}: "
                 f"{move['from']} -> {move['to']}{extra}")
            if measure_cpu or show_clock:
                used = {name: session.sample_cpu_seconds() for name, session in sessions.items()}
                if measure_cpu:
                    emit(f"CPU AFTER READY: A={used['A']:.6f}s | B={used['B']:.6f}s")
                if show_clock:
                    emit(f"CLOCK PREVIEW: A={display_seconds_left(used['A']):.3f}s "
                         f"| B={display_seconds_left(used['B']):.3f}s")
            if game.result is not None:
                break
        else:
            emit(f"DEMO STOP: {max_moves} moves reached; no tournament result declared.")
    # Both processes must exit normally before reporting this demo's final result.
    if game.result is not None:
        emit(f"RESULT: {game.result} | reason={game.reason} | moves={len(game.history)}")
    for side, session in sessions.items():
        emit(f"META {side}: session_cpu={session.metadata.get('time', '?')}s (includes Python startup)")
        if measure_cpu:
            emit(f"CPU AFTER READY {side}: used={session.cpu_after_ready_seconds:.6f}s")
        if show_clock:
            remaining = display_seconds_left(session.cpu_after_ready_seconds)
            emit(f"CLOCK PREVIEW {side}: remaining={remaining:.3f}s "
                 f"| used_cpu={session.cpu_after_ready_seconds:.6f}s")
    emit("CLEANUP: both bots stopped; boxes 0 and 1 removed.")
    return game


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--measure-cpu", action="store_true",
                        help="show cgroup CPU measured after the engine receives READY")
    parser.add_argument("--show-clock", action="store_true",
                        help="preview a 30s display clock mapped from a 5s CPU budget; no enforcement")
    args = parser.parse_args()
    try:
        game = run_match(args.seed, measure_cpu=args.measure_cpu, show_clock=args.show_clock)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"ISOLATED MATCH ERROR: {error}", file=sys.stderr)
        return 1
    return 0 if game.result is not None else 2


if __name__ == "__main__":
    sys.exit(main())
