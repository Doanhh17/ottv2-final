"""Produce small replay records from the Python Engine for JavaScript checks."""

import argparse
import copy
import json
import random
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ottv2.engine import Game, Piece, small_sample_game as sample_game
from ottv2.protocol import state_message


def record(game, moves, result=None):
    initial = state_message(game)
    for start, end in moves:
        game.play({"from": start, "to": end})
    winner, reason = result or (game.result, game.reason)
    return {"schema_version": 1, "initial_state": initial, "final_state": state_message(game),
            "history": game.history,
            "result": {"winner": winner, "reason": reason, "valid_move_count": len(game.history)},
            "limits": {"cpu_budget_seconds": 1.5, "response_limit_seconds": 1,
                       "memory_limit_mib_per_bot": 128,
                       **({'max_valid_moves': game.max_valid_moves} if game.max_valid_moves is not None else {}),
                       **({'move_limit_tiebreak':game.move_limit_tiebreak} if game.max_valid_moves is not None and game.move_limit_tiebreak != 'draw' else {})}}


def long_fixture(first='A', limit=70, moves=70, initial_state=None,
                 tiebreak='piece_count', preserve_material=False, prefix=()):
    """A deterministic legal walk that avoids wins and threefold repetition."""
    game = (sample_game(first) if initial_state is None else
            Game({p:Piece(**v) for p,v in initial_state['board'].items()},initial_state['goals'],
                 set(initial_state['bombs']),set(initial_state['obstacles']),first))
    start = copy.deepcopy(game)
    rng = random.Random(135)
    sequence = []
    for source, target in prefix:
        game.play({'from':source,'to':target})
        assert game.result is None
        sequence.append((source,target))
    for _ in range(moves-len(sequence)):
        candidates = game.legal_moves()
        rng.shuffle(candidates)
        for move in candidates:
            if preserve_material and (move['to'] in game.board or move['to'] in game.bombs):
                continue
            trial = copy.deepcopy(game)
            trial.play(move)
            if trial.result is None:
                game = trial
                sequence.append((move['from'], move['to']))
                break
        else:
            raise AssertionError('fixture cannot continue')
    initial = start
    initial.max_valid_moves = limit
    initial.move_limit_tiebreak = tiebreak
    return record(initial, sequence, None if limit is not None and moves >= limit else ('B', 'response_timeout'))


def material_fixture(first='A', tiebreak='piece_count', initial_state=None, limit=70):
    from ottv2.engine import sample_game as nine_piece_game
    if initial_state is None:
        initial_state=state_message(nine_piece_game(first))
    prefix=[('a1','a2'),('i9','i8'),('a2','a3'),('i8','i7'),
            ('a3','a4'),('i7','i6'),('a4','a5'),('i6','i5'),
            ('a5','b6'),('i5','h5'),('b6','c7'),('h5','g5'),
            ('c7','d8'),('g5','f6'),('d8','d9')]
    if first=='B':
        def mirror(square):return chr(ord('i')-(ord(square[0])-ord('a')))+str(10-int(square[1]))
        prefix=[(mirror(a),mirror(b)) for a,b in prefix]
    return long_fixture(first,initial_state=initial_state,tiebreak=tiebreak,limit=limit,moves=limit,
                        preserve_material=True,prefix=prefix)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = sample_game()
    fixtures = {
        "bomb": record(Game(base.board, base.goals, {"a2"}), [("a1", "a2")]),
        "goal": record(Game(base.board, {"A": "d1", "B": "a9"}), [("c1", "d1")]),
        "zero": record(sample_game(), [], ("B", "startup_timeout")),
        "b_first": record(sample_game("B"), [("g9", "f8")], ("A", "response_timeout")),
        "capture": record(Game({**base.board, "e4": Piece("A", "rock"),
                                "f5": Piece("B", "scissors")}, base.goals),
                          [("e4", "f5")], ("B", "response_timeout")),
    }
    # Both sides return their rock to the same square twice: initial is counted.
    cycle = [("a1", "a2"), ("i9", "i8"), ("a2", "a1"), ("i8", "i9")] * 2
    fixtures["repetition"] = record(sample_game(), cycle)
    fixtures['move_limit'] = long_fixture()
    fixtures['legacy_long'] = long_fixture(limit=None, moves=101)
    fixtures['material_a'] = material_fixture('A')
    fixtures['material_b'] = material_fixture('B')
    fixtures['legacy_capped_draw'] = material_fixture(tiebreak='draw')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixtures) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
