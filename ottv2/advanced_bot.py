"""Two bounded alpha-beta players using a compact board and reversible moves."""

from collections import Counter
from time import monotonic, process_time

from .protocol import validate_time_info

NEIGHBORS = tuple(tuple(y * 9 + x for y in range(max(0, i // 9 - 1), min(9, i // 9 + 2))
                        for x in range(max(0, i % 9 - 1), min(9, i % 9 + 2))
                        if y * 9 + x != i) for i in range(81))
KINDS = ("rock", "paper", "scissors")
WIN = 30000


def index(square):
    return (int(square[1]) - 1) * 9 + ord(square[0]) - 97


def square(i):
    return chr(97 + i % 9) + str(i // 9 + 1)


def owner(piece):
    return (piece - 1) // 3


def distance(a, b):
    return max(abs(a % 9 - b % 9), abs(a // 9 - b // 9))


class Position:
    def __init__(self, state):
        self.board = bytearray(81)
        self.counts = [0] * 6
        for pos, piece in state["board"].items():
            value = (0 if piece["side"] == "A" else 3) + KINDS.index(piece["kind"]) + 1
            self.board[index(pos)] = value
            self.counts[value - 1] += 1
        self.turn = 0 if state["turn"] == "A" else 1
        self.goals = tuple(index(state["goals"][side]) for side in ("A", "B"))
        self.bombs = sum(1 << index(pos) for pos in state["bombs"])
        self.obstacles = {index(pos) for pos in state["obstacles"]}

    def key(self):
        return bytes(self.board), self.bombs, self.turn

    def moves(self):
        moves = []
        for start, piece in enumerate(self.board):
            if not piece or owner(piece) != self.turn:
                continue
            for end in NEIGHBORS[start]:
                if end in self.obstacles:
                    continue
                target = self.board[end]
                if target and (owner(target) == self.turn or
                               (piece - 1) % 3 != ((target - 1) % 3 + 1) % 3):
                    continue
                moves.append((start, end))
        return moves

    def push(self, move):
        start, end = move
        piece, captured = self.board[start], self.board[end]
        bomb = bool(self.bombs & (1 << end))
        self.board[start] = 0
        if captured:
            self.counts[captured - 1] -= 1
        if bomb:
            self.counts[piece - 1] -= 1
            self.bombs ^= 1 << end
        self.board[end] = 0 if bomb else piece
        side = self.turn
        winner = side if not bomb and end == self.goals[side] else None
        if winner is None:
            for loser in (side, 1 - side):
                if 0 in self.counts[loser * 3:loser * 3 + 3]:
                    winner = 1 - loser
                    break
        self.turn = 1 - side
        return (piece, captured, bomb), winner

    def pop(self, move, undo):
        start, end = move
        piece, captured, bomb = undo
        self.turn = 1 - self.turn
        self.board[start], self.board[end] = piece, captured
        if captured:
            self.counts[captured - 1] += 1
        if bomb:
            self.counts[piece - 1] += 1
            self.bombs |= 1 << end


class Search:
    def __init__(self, position, style, cpu_limit, wall_limit, cpu_clock, wall_clock):
        self.p = position
        self.style = style
        self.cpu_clock, self.wall_clock = cpu_clock, wall_clock
        self.cpu_end, self.wall_end = cpu_clock() + cpu_limit, wall_clock() + wall_limit
        self.nodes = 0
        self.order_table = {}
        self.path = Counter({position.key(): 1})

    def check(self):
        if self.cpu_clock() >= self.cpu_end:
            raise TimeoutError("cpu")
        if self.wall_clock() >= self.wall_end:
            raise TimeoutError("wall")

    def evaluate(self):
        p = self.p
        values = []
        for side in (0, 1):
            counts = p.counts[side * 3:side * 3 + 3]
            own = [(i, v) for i, v in enumerate(p.board) if v and owner(v) == side]
            nearest = min(distance(i, p.goals[side]) for i, _ in own)
            value = sum(counts) * (90 if self.style == "guardian" else 65)
            value -= sum(65 if n == 1 else 10 if n == 2 else 0 for n in counts)
            value -= nearest * (8 if self.style == "guardian" else 18)
            # Penalize exposed pieces, especially the last of their kind.
            for pos, piece in own:
                attackers = [p.board[n] for n in NEIGHBORS[pos] if p.board[n] and owner(p.board[n]) != side
                             and (p.board[n] - 1) % 3 == ((piece - 1) % 3 + 1) % 3]
                if attackers:
                    value -= (180 if counts[(piece - 1) % 3] == 1 else
                              30 if self.style == "guardian" else 16)
            values.append(value)
        return values[p.turn] - values[1 - p.turn]

    def priority(self, move, preferred):
        start, end = move
        p = self.p
        return ((100000 if move == preferred else 0) +
                (10000 if end == p.goals[p.turn] else 0) -
                (20000 if p.bombs & (1 << end) else 0) +
                (1000 if p.board[end] else 0) - distance(end, p.goals[p.turn]))

    def negamax(self, depth, alpha, beta, ply=0, winner=None):
        self.nodes += 1
        if self.nodes % 32 == 0 or ply == 0:
            self.check()
        if winner is not None:
            return (WIN - ply) * (1 if winner == self.p.turn else -1), None
        key = self.p.key()
        if self.path[key] >= 3:
            return 0, None
        if depth == 0:
            return self.evaluate(), None
        moves = self.p.moves()
        if not moves:
            # No pass exists: blocked side eventually loses on its unchanged turn.
            return -WIN + ply, None
        preferred = self.order_table.get(key)
        moves.sort(key=lambda m: self.priority(m, preferred), reverse=True)
        best, choice = -WIN - 1, moves[0]
        for move in moves:
            undo, won = self.p.push(move)
            child_key = self.p.key()
            self.path[child_key] += 1
            try:
                score, _ = self.negamax(depth - 1, -beta, -alpha, ply + 1, won)
                score = -score
            finally:
                self.path[child_key] -= 1
                if not self.path[child_key]:
                    del self.path[child_key]
                self.p.pop(move, undo)
            if score > best:
                best, choice = score, move
            alpha = max(alpha, score)
            if alpha >= beta:
                break
        # Only cache ordering, not scores: repetition depends on the search path.
        if len(self.order_table) >= 6000:
            self.order_table.clear()
        self.order_table[key] = choice
        return best, choice


def advanced_move(state, rng, *, style="alpha", stats=None, cpu_clock=process_time, wall_clock=monotonic):
    if style not in ("alpha", "guardian"):
        raise ValueError("unknown advanced style")
    started_cpu, started_wall = cpu_clock(), wall_clock()
    info = validate_time_info(state["time"]) if "time" in state else None
    left = info["cpu_remaining_seconds"][state["you"]] if info else 5.0
    wall = info["response_remaining_seconds"] if info else 1.0
    cpu_slice = min(0.35, max(0, left - 0.03) / 24)
    wall_slice = min(0.7, max(0, wall - 0.08))
    p = Position(state)
    search = Search(p, style, max(0, cpu_slice - (cpu_clock() - started_cpu)),
                    max(0, wall_slice - (wall_clock() - started_wall)), cpu_clock, wall_clock)
    moves = p.moves()
    details = {"completed_depth": 0, "nodes": 0, "cutoff": "reserve",
               "cpu_slice_seconds": cpu_slice, "wall_slice_seconds": wall_slice, "style": style}
    if not moves:
        own = next(i for i, v in enumerate(p.board) if v and owner(v) == p.turn)
        chosen = own, own
        details["cutoff"] = "no_legal_move"
    else:
        moves.sort(key=lambda m: search.priority(m, None), reverse=True)
        chosen = rng.choice([m for m in moves if search.priority(m, None) == search.priority(moves[0], None)])
        if cpu_slice > 0 and wall_slice > 0:
            for depth in range(1, 9):
                try:
                    score, candidate = search.negamax(depth, -WIN - 1, WIN + 1)
                    search.check()
                except TimeoutError as error:
                    details["cutoff"] = str(error)
                    break
                chosen = candidate
                details["completed_depth"] = depth
                details["cutoff"] = "depth_limit"
                if abs(score) > WIN - 100:
                    details["cutoff"] = "forced_result"
                    break
    details.update(nodes=search.nodes, ordering_entries=len(search.order_table),
                   cpu_used_seconds=cpu_clock() - started_cpu)
    if stats is not None:
        stats.update(details)
    return {"from": square(chosen[0]), "to": square(chosen[1])}
