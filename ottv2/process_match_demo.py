"""One local match: two trusted bot processes, started once per match."""

import argparse
from contextlib import ExitStack
import random
import sys

from .engine import sample_game
from .sample_session import SampleSession


def run_match(seed=1, max_moves=1000, emit=print):
    first = random.Random(seed).choice(("A", "B"))
    game = sample_game(first)
    emit(f"OTTv2 PROCESS MATCH | seed={seed} | first={first}")
    emit("Bundled samples only | tournament clocks/sandbox: not implemented")
    with ExitStack() as stack:
        sessions = {}
        for side, strategy in (("A", "random"), ("B", "greedy")):
            session = stack.enter_context(SampleSession(strategy, seed))
            sessions[side] = session
            emit(f"START {side}: {strategy} | pid={session.process.pid}")
            emit(f"READY {side} | startup_wall={session.startup_wall_seconds:.3f}s")
        for number in range(1, max_moves + 1):
            if not game.legal_moves():
                emit("DEMO STOP: no legal move; future Worker retries until timeout.")
                break
            side = game.turn
            move = sessions[side].request_move(game)
            # Engine, rather than the bot, decides whether the move is legal.
            # Unexpected sample errors abort this demo; no tournament loss is assigned.
            event = game.play(move)
            extra = " BOMB" if event["bomb"] else ""
            if event["capture"]:
                extra += " CAPTURE=" + event["capture"]
            emit(f"{number:03d} {side} pid={sessions[side].process.pid}: "
                 f"{move['from']} -> {move['to']}{extra}")
            if game.result is not None:
                emit(f"RESULT: {game.result} | reason={game.reason} | moves={len(game.history)}")
                break
        else:
            emit(f"DEMO STOP: {max_moves} moves reached; no tournament result declared.")
    emit("CLEANUP: both sample bot processes stopped.")
    return game


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    try:
        game = run_match(args.seed)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"DEMO ERROR: {error}", file=sys.stderr)
        return 1
    return 0 if game.result is not None else 2


if __name__ == "__main__":
    sys.exit(main())
