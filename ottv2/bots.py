"""Two trusted in-process examples, not the future submission protocol."""

from .engine import coordinate


def random_bot(game, rng):
    moves = game.legal_moves()
    return rng.choice(moves) if moves else None


def greedy_bot(game, rng):
    moves = game.legal_moves()
    if not moves:
        return None
    gx, gy = coordinate(game.goals[game.turn])

    def score(move):
        end = move["to"]
        x, y = coordinate(end)
        if end in game.bombs:
            return -1000
        if end == game.goals[game.turn]:
            return 1000
        return (20 if end in game.board else 0) - max(abs(x - gx), abs(y - gy))

    best = max(map(score, moves))
    return rng.choice([m for m in moves if score(m) == best])
