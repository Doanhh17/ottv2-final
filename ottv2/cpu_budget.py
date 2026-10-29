"""Cumulative own-turn CPU and a separate wall deadline for each turn."""

import math
from queue import Empty
from time import monotonic

from .rules import CPU_BUDGET_SECONDS, RESPONSE_LIMIT_SECONDS

DEFAULT_CPU_BUDGET_SECONDS = CPU_BUDGET_SECONDS


class CpuBudgetExceeded(RuntimeError):
    def __init__(self, side, used, limit):
        self.side, self.used, self.limit = side, used, limit
        super().__init__(f"{side} used {used:.6f}s CPU on its own turns (budget {limit:g}s)")


class ResponseTimeout(RuntimeError):
    def __init__(self, side, used, limit):
        self.side, self.used, self.limit = side, used, limit
        super().__init__(f"{side} used {used:.6f}s wall time on this turn (limit {limit:g}s)")


class CpuBudgetGuard:
    def __init__(self, sessions, seconds=DEFAULT_CPU_BUDGET_SECONDS, response_seconds=RESPONSE_LIMIT_SECONDS, time_source=monotonic,
                 health_check=None):
        for name, value in (("CPU", seconds), ("response", response_seconds)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} budget must be a positive finite number")
        self.sessions = sessions
        self.seconds = seconds
        self.response_seconds = response_seconds
        self.time_source = time_source
        self.health_check = health_check
        self.used = {}
        self.wall_used = {}
        self.response_max = {}
        self.sent_time_info = {}
        self.turn_wall = 0.0
        self.active_side = None
        self.last_cpu = None
        self.last_wall = None

    def begin_turn(self, side):
        if self.active_side == side:
            return  # A repeated request must not reset the active turn's budget.
        if self.active_side is not None:
            raise RuntimeError("finish the current CPU turn before starting another")
        # Discard CPU spent before this turn, including any free pondering.
        self.last_cpu = self.sessions[side].sample_cpu_seconds()
        self.last_wall = self.time_source()
        self.turn_wall = 0.0
        self.active_side = side

    def finish_turn(self):
        readings = self.check()
        self.active_side = None
        self.last_cpu = None
        self.last_wall = None
        return readings

    def stop_bots(self):
        # Stop at the decision, rather than waiting a grace second during close.
        for session in self.sessions.values():
            if session.process.poll() is None:
                try:
                    session.process.terminate()
                except ProcessLookupError:
                    pass

    def check(self):
        for side in self.sessions:
            self.used.setdefault(side, 0.0)
            self.wall_used.setdefault(side, 0.0)
            self.response_max.setdefault(side, 0.0)
        side = self.active_side
        if side is not None:
            current = self.sessions[side].sample_cpu_seconds()
            if current < self.last_cpu:
                raise RuntimeError("CPU counter moved backwards during a turn")
            self.used[side] += current - self.last_cpu
            self.last_cpu = current
            now = self.time_source()
            if now < self.last_wall:
                raise RuntimeError("monotonic clock moved backwards during a turn")
            elapsed = now - self.last_wall
            self.wall_used[side] += elapsed
            self.turn_wall += elapsed
            self.response_max[side] = max(self.response_max[side], self.turn_wall)
            self.last_wall = now
        if side is not None and self.used[side] >= self.seconds:
            self.stop_bots()
            raise CpuBudgetExceeded(side, self.used[side], self.seconds)
        if side is not None and self.turn_wall >= self.response_seconds:
            self.stop_bots()
            raise ResponseTimeout(side, self.turn_wall, self.response_seconds)
        if self.health_check is not None:
            self.health_check(side)
        return dict(self.used)

    def time_info(self):
        if self.active_side is None:
            raise RuntimeError("a clock snapshot requires an active turn")
        self.check()
        return {
            "cpu_budget_seconds": self.seconds,
            "cpu_remaining_seconds": {side: max(0.0, self.seconds - self.used[side])
                                      for side in self.sessions},
            "response_limit_seconds": self.response_seconds,
            "response_remaining_seconds": max(0.0, self.response_seconds - self.turn_wall),
        }

    def request_move(self, side, game):
        self.begin_turn(side)
        info = self.time_info()
        session = self.sessions[side]
        session.send_state(game, time_info=info)
        self.sent_time_info[side] = info
        while True:
            self.check()
            # A retry on this turn must retain the original deadline.
            remaining = self.response_seconds - self.turn_wall
            try:
                reply = session._replies.get(timeout=min(0.01, remaining))
            except Empty:
                continue
            self.check()
            return session.decode_reply(reply)
