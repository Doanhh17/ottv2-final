import unittest
from unittest.mock import patch

from ottv2.engine import sample_game
from ottv2.process_match_demo import run_match
from ottv2.sample_session import SampleSession


class ProcessMatchTests(unittest.TestCase):
    def test_full_match_starts_exactly_two_processes_and_cleans_up(self):
        sessions = []

        def create_session(*args, **kwargs):
            session = SampleSession(*args, **kwargs)
            sessions.append(session)
            return session

        lines = []
        with patch("ottv2.process_match_demo.SampleSession", side_effect=create_session):
            game = run_match(emit=lines.append)
        self.assertIsNotNone(game.result)
        self.assertEqual(len(sessions), 2)
        for session, side in zip(sessions, ("A", "B")):
            self.assertIsNotNone(session.process.poll())
            turns = [line for line in lines if f" {side} pid=" in line]
            self.assertGreater(len(turns), 1)
            self.assertTrue(all(f"pid={session.process.pid}:" in line for line in turns))

    def test_one_live_bot_handles_repeated_requests_without_restart(self):
        game = sample_game()
        with SampleSession("random") as session:
            original = session.process
            for _ in range(3):
                game.validate_move(session.request_move(game))
                self.assertIs(session.process, original)
                self.assertIsNone(original.poll())
        self.assertEqual(original.returncode, 0)

    def test_sample_process_is_closed_on_exception(self):
        session = SampleSession("greedy")
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with session:
                session.request_move(sample_game())
                raise RuntimeError("test failure")
        self.assertIsNotNone(session.process.poll())
        self.assertTrue(session.process.stdout.closed)
        self.assertTrue(session.process.stderr.closed)

    def test_demo_move_cap_does_not_declare_a_draw(self):
        game = run_match(max_moves=1, emit=lambda _: None)
        self.assertIsNone(game.result)
        self.assertEqual(len(game.history), 1)


if __name__ == "__main__":
    unittest.main()
