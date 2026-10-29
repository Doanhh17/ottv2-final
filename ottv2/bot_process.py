"""Trusted sample bot process; read JSON lines until stdin closes."""

import argparse
import json
import random
import sys

from .engine import BEATS, coordinate
from .protocol import ready_message, validate_time_info


def choose_move(state, strategy, rng, search_stats=None):
    if strategy in ("alpha", "guardian"):
        from .advanced_bot import advanced_move
        return advanced_move(state, rng, style=strategy, stats=search_stats)
    if strategy == "search":
        from .search_bot import search_move
        return search_move(state, rng, stats=search_stats)
    board = state["board"]
    side = state["you"]
    obstacles = set(state["obstacles"])
    moves = []
    for start, piece in sorted(board.items()):
        if piece["side"] != side:
            continue
        x, y = coordinate(start)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (dx == dy == 0) or not (0 <= x + dx < 9 and 0 <= y + dy < 9):
                    continue
                end = chr(97 + x + dx) + str(y + dy + 1)
                target = board.get(end)
                if end in obstacles:
                    continue
                if target and (target["side"] == side or BEATS[piece["kind"]] != target["kind"]):
                    continue
                moves.append({"from": start, "to": end})
    if not moves:
        # No legal move: a well-formed illegal attempt, never null or a pass.
        # cpu_trial retries the same state until the original turn clock expires.
        own = next(pos for pos, p in sorted(board.items()) if p["side"] == side)
        return {"from": own, "to": own}
    if strategy == "random":
        return rng.choice(moves)
    gx, gy = coordinate(state["goals"][side])

    def score(move):
        end = move["to"]
        x, y = coordinate(end)
        if end in state["bombs"]:
            return -1000
        if end == state["goals"][side]:
            return 1000
        return (20 if end in board else 0) - max(abs(x - gx), abs(y - gy))

    best = max(map(score, moves))
    return rng.choice([move for move in moves if score(move) == best])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("random", "greedy", "search", "retry-demo", "alpha", "guardian"), required=True)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    first_attempt = True
    # Imports and initialization are finished. Flush before waiting for any state.
    print(json.dumps(ready_message()), flush=True)
    for line in sys.stdin:
        try:
            state = json.loads(line)
            if state["protocol_version"] != 1 or state["type"] != "state":
                raise ValueError("unsupported message")
            if state["you"] != state["turn"]:
                raise ValueError("only send a state when it is this bot's turn")
            if "time" in state:
                # The search sample uses this snapshot to plan a bounded calculation.
                state["time"] = validate_time_info(state["time"])
            stats = {} if args.strategy in ("search", "alpha", "guardian") else None
            if args.strategy == "retry-demo" and first_attempt:
                # Trusted diagnostic bot: one well-formed illegal move, then ordinary random play.
                own = next(pos for pos, piece in sorted(state["board"].items())
                           if piece["side"] == state["you"])
                move = {"from": own, "to": own}
            else:
                strategy = "random" if args.strategy == "retry-demo" else args.strategy
                move = choose_move(state, strategy, rng, search_stats=stats)
            first_attempt = False
            print(json.dumps(move), flush=True)
            if stats is not None:
                print("SEARCH INFO: " + json.dumps(stats), file=sys.stderr, flush=True)
        except (ValueError, KeyError, TypeError, StopIteration) as error:
            print(f"BOT INPUT ERROR: {error}", file=sys.stderr, flush=True)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
