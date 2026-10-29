from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ottv2.engine import sample_game
from ottv2.isolated_session import IsolatedSampleSession
from ottv2.sample_session import BotProcessError, SampleSession


class RuntimeTests(unittest.TestCase):
    def test_eof_is_a_bot_channel_failure(self):
        with self.assertRaisesRegex(BotProcessError, "closed its output"):
            SampleSession("random").decode_reply(None)

    def test_broken_input_pipe_is_a_bot_channel_failure(self):
        session = SampleSession("random")
        session.ready = True
        session.process = SimpleNamespace(poll=lambda: None, stdin=Mock())
        session.process.stdin.flush.side_effect = BrokenPipeError("closed")
        with self.assertRaises(BotProcessError):
            session.send_state(sample_game())

    def test_other_pipe_errors_remain_system_errors(self):
        session = SampleSession("random")
        session.ready = True
        session.process = SimpleNamespace(poll=lambda: None, stdin=Mock())
        session.process.stdin.write.side_effect = OSError("host write failure")
        with self.assertRaises(RuntimeError) as caught:
            session.send_state(sample_game())
        self.assertNotIsInstance(caught.exception, BotProcessError)

    def test_missing_process_is_a_setup_error(self):
        with self.assertRaises(RuntimeError) as caught:
            SampleSession("random").check_process()
        self.assertNotIsInstance(caught.exception, BotProcessError)

    def test_stopped_process_is_a_bot_failure(self):
        session = SampleSession("random")
        session.process = SimpleNamespace(poll=lambda: 1)
        with self.assertRaisesRegex(BotProcessError, "process stopped"):
            session.check_process()

    def test_runtime_details_use_confirmed_exit_or_signal_metadata(self):
        for meta, returncode, expected in (
            ({"exitcode": "0"}, 0, "exitcode=0"),
            ({"status": "RE", "exitcode": "7"}, 1, "exitcode=7"),
            ({"status": "SG", "exitsig": "15"}, 1, "signal=15"),
        ):
            with self.subTest(meta=meta):
                session = IsolatedSampleSession("random", 0)
                session.metadata = meta
                session.process = SimpleNamespace(returncode=returncode)
                self.assertIn(expected, session.runtime_error_detail("bot stopped"))

    def test_internal_resource_or_missing_metadata_is_not_runtime_loss(self):
        for meta, returncode in (
            ({}, 1),
            ({"status": "XX", "message": "host error"}, 2),
            ({"status": "TO"}, 1),
            ({"status": "SG", "exitsig": "9", "cg-oom-killed": "1"}, 1),
            ({"status": "RE"}, 1),
            ({"status": "RE", "exitcode": "0"}, 1),
            ({"status": "SG", "exitsig": "0"}, 1),
            ({"exitcode": "0"}, 2),
        ):
            with self.subTest(meta=meta, returncode=returncode):
                session = IsolatedSampleSession("random", 0)
                session.metadata = meta
                session.process = SimpleNamespace(returncode=returncode)
                with self.assertRaises(RuntimeError):
                    session.runtime_error_detail("bot stopped")

    def test_stderr_detail_is_bounded_and_kept_on_one_log_line(self):
        session = IsolatedSampleSession("random", 0)
        session.process = SimpleNamespace(returncode=1)
        session.metadata = {"status": "RE", "exitcode": "1"}
        session._errors = deque(["x" * 10000 + "\nRuntimeError: probe crash\n"])
        detail = session.runtime_error_detail("closed output")
        self.assertIn("RuntimeError: probe crash", detail)
        self.assertNotIn("\n", detail)
        self.assertLess(len(detail), 2200)
