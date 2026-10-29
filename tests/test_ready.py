from queue import Empty
import unittest
from unittest.mock import patch

from ottv2.protocol import validate_ready
from ottv2.sample_session import BotStartupError, SampleSession


class ReadyTests(unittest.TestCase):
    def test_startup_default_is_five_wall_seconds(self):
        self.assertEqual(SampleSession("random").startup_timeout, 5)

    def test_bad_startup_json_is_classified_and_bot_is_closed(self):
        session = SampleSession("random")
        with patch.object(session._replies, "get", return_value="{oops\n"):
            with self.assertRaises(BotStartupError) as caught:
                with session:
                    self.fail("bad JSON must not reach READY")
        self.assertEqual(caught.exception.reason, "startup_format_error")
        self.assertIsNotNone(session.process.poll())
        self.assertTrue(session.process.stdout.closed)

    def test_host_output_read_failure_is_not_a_bot_startup_failure(self):
        session = SampleSession("random")
        with patch.object(session._replies, "get", return_value=OSError("test host read failure")):
            with self.assertRaisesRegex(RuntimeError, "host read failure") as caught:
                with session:
                    self.fail("host read error must not reach READY")
        self.assertNotIsInstance(caught.exception, BotStartupError)
        self.assertIsNotNone(session.process.poll())
        self.assertTrue(session.process.stdout.closed)

    def test_bot_reports_ready_before_receiving_any_state(self):
        with SampleSession("random") as session:
            self.assertTrue(session.ready)
            self.assertIsNone(session.process.poll())
            self.assertGreater(session.startup_wall_seconds, 0)
            self.assertLessEqual(session.startup_wall_seconds, 5)

    def test_wrong_ready_stops_startup_and_closes_bot(self):
        session = SampleSession("greedy")
        with patch.object(session, "_receive_json", return_value={"from": "a1", "to": "a2"}):
            with self.assertRaisesRegex(BotStartupError, "expected READY") as caught:
                with session:
                    self.fail("must not enter a session without READY")
        self.assertEqual(caught.exception.reason, "startup_format_error")
        self.assertFalse(session.ready)
        self.assertIsNotNone(session.process.poll())
        self.assertTrue(session.process.stdout.closed)

    def test_missing_ready_hits_deadline_and_closes_bot(self):
        session = SampleSession("random")
        # Simulate the queue deadline without making the test sleep for 5 seconds.
        with patch.object(session._replies, "get", side_effect=Empty):
            with self.assertRaisesRegex(BotStartupError, "did not send READY") as caught:
                with session:
                    self.fail("must not enter a session after deadline")
        self.assertEqual(caught.exception.reason, "startup_timeout")
        self.assertFalse(session.ready)
        self.assertIsNotNone(session.process.poll())

    def test_ready_schema_rejects_wrong_types_and_versions(self):
        for message in (None, "READY", {}, {"type": "ready", "protocol_version": True},
                        {"type": "ready", "protocol_version": 2},
                        {"type": "state", "protocol_version": 1}):
            with self.subTest(message=message), self.assertRaises(ValueError):
                validate_ready(message)


if __name__ == "__main__":
    unittest.main()
