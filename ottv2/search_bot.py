"""Small minimax sample with iterative deepening and cooperative CPU/wall stops."""

from copy import deepcopy
from time import monotonic, process_time

from .engine import Game, Piece, coordinate
from .protocol import validate_time_info

CPU_RESERVE_SECONDS = 0.015
WALL_RESERVE_SECONDS = 0.05


class _SearchLimit(Exception):
    pass


def allocate_search_time(info, side):
    info = validate_time_info(info)
    # A trial policy: spread the remaining CPU over about twenty future own turns.
    available_cpu = max(0.0, info["cpu_remaining_seconds"][side] - CPU_RESERVE_SECONDS)
    available_wall = max(0.0, info["response_remaining_seconds"] - WALL_RESERVE_SECONDS)
    return min(0.08, available_cpu / 20), min(0.2, available_wall)


def evaluate(game, side):
    opponent = "B" if side == "A" else "A"
    if game.result is not None:
        return 10000 if game.result == side else (0 if game.result == "draw" else -10000)

    def value(owner):
        pieces = [(pos, piece) for pos, piece in game.board.items() if piece.side == owner]
        gx, gy = coordinate(game.goals[owner])
        distance = min(max(abs(coordinate(pos)[0] - gx), abs(coordinate(pos)[1] - gy))
                       for pos, _ in pieces)
        return 30 * len(pieces) - 3 * distance

    return value(side) - value(opponent)


def _move_priority(game, move):
    end = move["to"]
    if end in game.bombs:
        return -1000
    if end == game.goals[game.turn]:
        return 1000
    gx, gy = coordinate(game.goals[game.turn])
    x, y = coordinate(end)
    return (20 if end in game.board else 0) - max(abs(x - gx), abs(y - gy))


def _minimax(game, side, depth, alpha, beta, check):
    check()
    if game.result is not None or depth == 0:
        return evaluate(game, side)
    moves = game.legal_moves()
    if not moves:
        return evaluate(game, side)
    maximizing = game.turn == side
    best = -float("inf") if maximizing else float("inf")
    for move in sorted(moves, key=lambda item: _move_priority(game, item), reverse=True):
        check()
        child = deepcopy(game)
        child.play(move)
        score = _minimax(child, side, depth - 1, alpha, beta, check)
        if maximizing:
            best = max(best, score)
            alpha = max(alpha, best)
        else:
            best = min(best, score)
            beta = min(beta, best)
        if beta <= alpha:
            break
    return best


def _search_depth(game, moves, depth, rng, check):
    best_score = -float("inf")
    best_moves = []
    for move in moves:
        check()
        child = deepcopy(game)
        child.play(move)
        # Full root windows keep tied root scores exact rather than mixing in bounds.
        score = _minimax(child, game.turn, depth - 1, -float("inf"), float("inf"), check)
        if score > best_score:
            best_score, best_moves = score, [move]
        elif score == best_score:
            best_moves.append(move)
    check()
    return rng.choice(best_moves), best_score


def search_move(state, rng, *, cpu_clock=process_time, wall_clock=monotonic, stats=None):
    started_cpu, started_wall = cpu_clock(), wall_clock()
    timing = state.get("time")
    cpu_slice, wall_slice = allocate_search_time(timing, state["you"]) if timing is not None else (None, None)
    details = {"cpu_slice_seconds": cpu_slice, "wall_slice_seconds": wall_slice,
               "completed_depth": 0, "cutoff": "none"}

    def check():
        if cpu_slice is not None and cpu_clock() - started_cpu >= cpu_slice:
            raise _SearchLimit("cpu")
        if wall_slice is not None and wall_clock() - started_wall >= wall_slice:
            raise _SearchLimit("wall")

    def finish(move):
        if stats is not None:
            stats.update(details)
        return move

    game = Game({pos: Piece(**piece) for pos, piece in state["board"].items()},
                state["goals"], set(state["bombs"]), set(state["obstacles"]), state["turn"])
    moves = game.legal_moves()
    if not moves:
        own = next(pos for pos, piece in sorted(game.board.items()) if piece.side == game.turn)
        details["cutoff"] = "no_legal_move"
        return finish({"from": own, "to": own})

    # Pick a legal fallback before beginning any expensive search.
    priorities = [_move_priority(game, move) for move in moves]
    best_priority = max(priorities)
    selected = rng.choice([move for move, priority in zip(moves, priorities) if priority == best_priority])
    if selected["to"] == game.goals[game.turn]:
        details["cutoff"] = "immediate_goal"
        return finish(selected)
    if cpu_slice == 0 or wall_slice == 0:
        details["cutoff"] = "reserve"
        return finish(selected)

    ordered = sorted(moves, key=lambda move: _move_priority(game, move), reverse=True)
    for depth in range(1, (4 if timing is not None else 2) + 1):
        try:
            candidate, score = _search_depth(game, ordered, depth, rng, check)
        except _SearchLimit as error:
            details["cutoff"] = str(error)
            break
        selected = candidate
        details["completed_depth"] = depth
        if score == 10000:
            details["cutoff"] = "winning_line"
            break
    return finish(selected)
