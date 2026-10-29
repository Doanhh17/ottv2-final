"""Pure game rules: no bot processes, clocks, network or ranking."""

from collections import Counter
from dataclasses import dataclass, field

SIDES = ("A", "B")
KINDS = ("rock", "paper", "scissors")
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}


class FormatError(ValueError):
    """Malformed bot response: the match runner must declare a loss."""


class IllegalMove(ValueError):
    """Well-formed but illegal move: retry without changing the state."""


def coordinate(value):
    if not isinstance(value, str) or len(value) != 2:
        raise FormatError("coordinate must be a string such as e4")
    if value[0] not in "abcdefghi" or value[1] not in "123456789":
        raise FormatError("coordinate must be within a1..i9, lowercase")
    return ord(value[0]) - ord("a"), int(value[1]) - 1


@dataclass(frozen=True)
class Piece:
    side: str
    kind: str


@dataclass
class Game:
    board: dict[str, Piece]
    goals: dict[str, str]
    bombs: set[str] = field(default_factory=set)
    obstacles: set[str] = field(default_factory=set)
    turn: str = "A"
    max_valid_moves: int | None = None
    move_limit_tiebreak: str = 'draw'
    result: str | None = field(default=None, init=False)
    reason: str | None = field(default=None, init=False)
    history: list[dict] = field(default_factory=list, init=False)
    _seen: Counter = field(default_factory=Counter, init=False, repr=False)

    def __post_init__(self):
        if self.move_limit_tiebreak not in ('draw', 'piece_count'):
            raise ValueError('unknown move-limit tiebreak')
        if self.max_valid_moves is not None and (type(self.max_valid_moves) is not int
                or not 1 <= self.max_valid_moves <= 1000):
            raise ValueError("max_valid_moves must be an integer from 1 to 1000, or None")
        # Copy input containers so callers cannot mutate them accidentally.
        self.board = dict(self.board)
        self.goals = dict(self.goals)
        self.bombs = set(self.bombs)
        self.obstacles = set(self.obstacles)
        if self.turn not in SIDES or set(self.goals) != set(SIDES):
            raise ValueError("map needs sides A/B, two goals and a valid turn")
        for pos in set(self.board) | self.bombs | self.obstacles | set(self.goals.values()):
            coordinate(pos)
        if self.goals["A"] == self.goals["B"]:
            raise ValueError("goals must be distinct")
        if self.obstacles & (set(self.board) | self.bombs | set(self.goals.values())):
            raise ValueError("obstacles overlap another map object")
        if self.bombs & (set(self.board) | set(self.goals.values())):
            raise ValueError("demo maps forbid bombs on pieces or goals")
        for piece in self.board.values():
            if piece.side not in SIDES or piece.kind not in KINDS:
                raise ValueError("unknown side or piece kind")
        for side in SIDES:
            if {p.kind for p in self.board.values() if p.side == side} != set(KINDS):
                raise ValueError("each side must start with all three kinds")
            if self.goals[side] in self.board and self.board[self.goals[side]].side == side:
                raise ValueError("a side cannot start on its own winning goal")
        self._seen[self.position_key()] = 1

    def position_key(self):
        return (tuple(sorted((pos, p.side, p.kind) for pos, p in self.board.items())),
                tuple(sorted(self.bombs)), self.turn)

    def validate_move(self, move):
        if not isinstance(move, dict) or set(move) != {"from", "to"}:
            raise FormatError("move needs exactly from and to")
        start, end = move["from"], move["to"]
        x1, y1 = coordinate(start)
        x2, y2 = coordinate(end)
        if self.result is not None:
            raise IllegalMove("game has ended")
        piece = self.board.get(start)
        if piece is None or piece.side != self.turn:
            raise IllegalMove("source is not your piece")
        if max(abs(x1 - x2), abs(y1 - y2)) != 1:
            raise IllegalMove("move exactly one square in any direction")
        if end in self.obstacles:
            raise IllegalMove("destination is an obstacle")
        target = self.board.get(end)
        if target and (target.side == piece.side or BEATS[piece.kind] != target.kind):
            raise IllegalMove("destination contains a piece you cannot capture")

    def legal_moves(self):
        if self.result is not None:
            return []
        moves = []
        for start, piece in sorted(self.board.items()):
            if piece.side != self.turn:
                continue
            x, y = coordinate(start)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if (dx == dy == 0) or not (0 <= x + dx < 9 and 0 <= y + dy < 9):
                        continue
                    move = {"from": start, "to": chr(97 + x + dx) + str(y + dy + 1)}
                    try:
                        self.validate_move(move)
                    except IllegalMove:
                        continue
                    moves.append(move)
        return moves

    def play(self, move):
        # Everything that can reject a move happens before any mutation.
        self.validate_move(move)
        start, end = move["from"], move["to"]
        side = self.turn
        opponent = "B" if side == "A" else "A"
        piece = self.board.pop(start)
        captured = self.board.pop(end, None)
        exploded = end in self.bombs
        if exploded:
            self.bombs.remove(end)
        else:
            self.board[end] = piece
        if not exploded and end == self.goals[side]:
            self.result, self.reason = side, "goal"
        else:
            for loser in (side, opponent):
                kinds = {p.kind for p in self.board.values() if p.side == loser}
                if kinds != set(KINDS):
                    self.result = opponent if loser == side else side
                    self.reason = "missing_kind"
                    break
        self.turn = opponent
        key = self.position_key()
        self._seen[key] += 1
        at_limit = self.max_valid_moves is not None and len(self.history) + 1 >= self.max_valid_moves
        if self.result is None and at_limit and self.move_limit_tiebreak == 'piece_count':
            counts = Counter(piece.side for piece in self.board.values())
            if counts['A'] == counts['B']:
                self.result, self.reason = 'draw', 'move_limit'
            else:
                self.result = 'A' if counts['A'] > counts['B'] else 'B'
                self.reason = 'material_advantage'
        if self.result is None and self._seen[key] >= 3:
            self.result, self.reason = "draw", "threefold_repetition"
        if self.result is None and at_limit:
            self.result, self.reason = "draw", "move_limit"
        event = {"side": side, **move, "capture": captured.kind if captured else None,
                 "bomb": exploded, "result": self.result, "reason": self.reason}
        self.history.append(event)
        return dict(event)


def sample_game(first="A"):
    """Nine pieces per side, three of each kind; illustrative symmetric map."""
    board = {}
    for index, column in enumerate("abcdefghi"):
        kind = KINDS[index % len(KINDS)]
        board[column + "1"] = Piece("A", kind)
        board[chr(ord("i") - index) + "9"] = Piece("B", kind)
    return Game(board, {"A": "i9", "B": "a1"}, {"d5", "f5"}, {"e5"}, first)


def small_sample_game(first="A"):
    """Legacy six-piece fixture for focused regression tests and old records."""
    return Game(
        board={"a1": Piece("A", "rock"), "b1": Piece("A", "paper"),
               "c1": Piece("A", "scissors"), "i9": Piece("B", "rock"),
               "h9": Piece("B", "paper"), "g9": Piece("B", "scissors")},
        goals={"A": "i9", "B": "a1"}, bombs={"d5", "f5"},
        obstacles={"e5"}, turn=first)
