from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from ottv2.engine import small_sample_game
from ottv2.isolated_match_demo import run_match
from ottv2.isolated_session import BOX_ROOT, IsolatedSampleSession


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/local/bin/isolate").is_file(),
                     "requires the configured Ubuntu Isolate environment")
class IsolatedMatchTests(unittest.TestCase):
    def setUp(self):
        mapping = patch("ottv2.isolated_match_demo.sample_game", side_effect=small_sample_game)
        mapping.start()
        self.addCleanup(mapping.stop)

    def assert_boxes_removed(self):
        self.assertFalse((BOX_ROOT / "0").exists())
        self.assertFalse((BOX_ROOT / "1").exists())

    def test_complete_match_keeps_two_sessions_and_cleans_up(self):
        sessions = []

        def create(*args):
            session = IsolatedSampleSession(*args)
            sessions.append(session)
            return session

        with patch("ottv2.isolated_match_demo.IsolatedSampleSession", side_effect=create):
            game = run_match(emit=lambda _: None)
        self.assertEqual((game.result, game.reason, len(game.history)), ("B", "missing_kind", 24))
        self.assertEqual(len(sessions), 2)
        self.assertNotEqual(sessions[0].process.pid, sessions[1].process.pid)
        for session in sessions:
            self.assertEqual(session.process.returncode, 0)
            self.assertEqual(session.metadata.get("exitcode"), "0")
        self.assert_boxes_removed()

    def test_controller_error_closes_both_bots(self):
        def emit(line):
            if line.startswith("001 "):
                raise RuntimeError("test match controller failure")

        with self.assertRaisesRegex(RuntimeError, "test match controller failure"):
            run_match(emit=emit)
        self.assert_boxes_removed()

    def test_cpu_after_ready_is_measured_for_both_bots(self):
        sessions = []

        def create(*args):
            session = IsolatedSampleSession(*args)
            sessions.append(session)
            return session

        lines = []
        with patch("ottv2.isolated_match_demo.IsolatedSampleSession", side_effect=create):
            game = run_match(emit=lines.append, measure_cpu=True)
        self.assertEqual(game.result, "B")
        for session in sessions:
            self.assertGreater(session.cpu_meter.baseline_usec, 0)
            self.assertGreaterEqual(session.cpu_after_ready_seconds, 0)
        self.assertTrue(any(line.startswith("CPU AFTER READY A: used=") for line in lines))
        self.assertTrue(any(line.startswith("CPU AFTER READY B: used=") for line in lines))
        self.assert_boxes_removed()

    def test_second_session_start_failure_closes_first(self):
        sessions = []

        def create(strategy, box_id, seed):
            if box_id == 1:
                raise RuntimeError("test second bot startup failure")
            session = IsolatedSampleSession(strategy, box_id, seed)
            sessions.append(session)
            return session

        with patch("ottv2.isolated_match_demo.IsolatedSampleSession", side_effect=create):
            with self.assertRaisesRegex(RuntimeError, "test second bot startup failure"):
                run_match(emit=lambda _: None)
        self.assertIsNotNone(sessions[0].process.poll())
        self.assert_boxes_removed()

    def test_clock_preview_reports_both_sides_without_changing_game_result(self):
        lines = []
        game = run_match(emit=lines.append, show_clock=True)
        self.assertEqual((game.result, len(game.history)), ("B", 24))
        readings = [line for line in lines if line.startswith("CLOCK PREVIEW: ")]
        self.assertEqual(len(readings), 24)
        for side in ("A", "B"):
            final = next(line for line in lines if line.startswith(f"CLOCK PREVIEW {side}: "))
            remaining = float(final.split("remaining=", 1)[1].split("s", 1)[0])
            self.assertGreaterEqual(remaining, 0)
            self.assertLessEqual(remaining, 30)
        self.assert_boxes_removed()


if __name__ == "__main__":
    unittest.main()
