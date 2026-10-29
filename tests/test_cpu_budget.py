from types import SimpleNamespace
import unittest

from ottv2.cpu_budget import CpuBudgetExceeded, CpuBudgetGuard, ResponseTimeout


class FakeProcess:
    def __init__(self):
        self.stopped = False

    def poll(self):
        return None

    def terminate(self):
        self.stopped = True


def session(used):
    result = SimpleNamespace(raw=used, process=FakeProcess())
    result.sample_cpu_seconds = lambda: result.raw
    return result


class CpuBudgetTests(unittest.TestCase):
    def test_time_snapshots_preserve_cpu_across_turns_and_do_not_reset_retry_deadline(self):
        sessions = {"A": session(0), "B": session(0)}
        now = [0]
        guard = CpuBudgetGuard(sessions, time_source=lambda: now[0])
        with self.assertRaisesRegex(RuntimeError, "active turn"):
            guard.time_info()
        guard.begin_turn("A")
        sessions["A"].raw = 0.125
        now[0] = 0.25
        first = guard.time_info()
        self.assertEqual(first["cpu_remaining_seconds"], {"A": 6.875, "B": 7.0})
        self.assertEqual(first["response_remaining_seconds"], 2.25)
        guard.begin_turn("A")
        now[0] = 0.5
        self.assertEqual(guard.time_info()["response_remaining_seconds"], 2.0)
        guard.finish_turn()
        sessions["A"].raw = 20  # Pondering must not reduce the frozen A clock.
        guard.begin_turn("B")
        second = guard.time_info()
        self.assertEqual(second["cpu_remaining_seconds"], {"A": 6.875, "B": 7.0})
        self.assertEqual(second["response_remaining_seconds"], 2.5)
        first["cpu_remaining_seconds"]["A"] = 0
        self.assertEqual(guard.time_info()["cpu_remaining_seconds"]["A"], 6.875)

    def test_invalid_budgets_are_rejected(self):
        for value in (0, -1, float("nan"), float("inf"), True, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CpuBudgetGuard({}, value)
            with self.subTest(wall=value), self.assertRaises(ValueError):
                CpuBudgetGuard({}, response_seconds=value)

    def test_response_deadline_restarts_only_after_a_completed_turn(self):
        sessions = {"A": session(0), "B": session(0)}
        now = [0]
        guard = CpuBudgetGuard(sessions, response_seconds=1, time_source=lambda: now[0])
        guard.begin_turn("A")
        now[0] = 0.75
        guard.finish_turn()
        guard.begin_turn("B")
        now[0] = 1.5
        guard.finish_turn()
        now[0] = 3  # Idle time between turns is also excluded.
        guard.begin_turn("A")
        now[0] = 3.75
        guard.begin_turn("A")  # A retry cannot reset either clock.
        guard.check()
        self.assertEqual(guard.wall_used, {"A": 1.5, "B": 0.75})
        now[0] = 4
        with self.assertRaises(ResponseTimeout) as caught:
            guard.check()
        self.assertEqual((caught.exception.side, caught.exception.used), ("A", 1))
        self.assertEqual(guard.response_max, {"A": 1, "B": 0.75})
        self.assertEqual(guard.used, {"A": 0, "B": 0})
        self.assertTrue(all(s.process.stopped for s in sessions.values()))

    def test_pondering_is_free_even_above_the_budget(self):
        sessions = {"A": session(3), "B": session(4)}
        guard = CpuBudgetGuard(sessions, 1)
        self.assertEqual(guard.check(), {"A": 0, "B": 0})
        guard.begin_turn("A")
        sessions["A"].raw += 0.5
        sessions["B"].raw += 2
        self.assertEqual(guard.finish_turn(), {"A": 0.5, "B": 0})
        sessions["A"].raw += 2
        guard.begin_turn("B")
        sessions["B"].raw += 0.25
        self.assertEqual(guard.finish_turn(), {"A": 0.5, "B": 0.25})
        self.assertFalse(any(s.process.stopped for s in sessions.values()))

    def test_own_turns_accumulate_until_exact_budget_expires(self):
        sessions = {"A": session(0), "B": session(0)}
        guard = CpuBudgetGuard(sessions, 1)
        guard.begin_turn("A")
        sessions["A"].raw = 0.5
        guard.finish_turn()
        sessions["A"].raw = 10  # Free CPU between A's turns.
        guard.begin_turn("A")
        sessions["A"].raw = 10.5
        with self.assertRaises(CpuBudgetExceeded) as caught:
            guard.check()
        self.assertEqual(caught.exception.side, "A")
        self.assertEqual(caught.exception.used, 1)
        self.assertTrue(all(s.process.stopped for s in sessions.values()))

    def test_repeated_begin_does_not_reset_active_cpu(self):
        sessions = {"A": session(0), "B": session(0)}
        guard = CpuBudgetGuard(sessions, 1)
        guard.begin_turn("A")
        sessions["A"].raw = 0.75
        guard.begin_turn("A")
        self.assertEqual(guard.check()["A"], 0.75)
        with self.assertRaisesRegex(RuntimeError, "finish the current"):
            guard.begin_turn("B")
