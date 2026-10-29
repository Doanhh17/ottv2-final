import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ottv2.engine import FormatError, sample_game
from ottv2.sample_session import SampleSession


class ReplyFormatTests(unittest.TestCase):
    def test_json_decode_errors_are_format_errors(self):
        session = SampleSession("random")
        for reply in ("{oops\n", "debug message\n", "\n", '{"from":"a1"} trailing\n'):
            with self.subTest(reply=reply), self.assertRaises(FormatError):
                session.decode_reply(reply)

    def test_deeply_nested_json_cannot_be_accepted_as_a_move(self):
        with self.assertRaises(FormatError):
            move = SampleSession("random").decode_reply("[" * 10000 + "0" + "]" * 10000)
            sample_game().validate_move(move)

    def test_io_errors_and_eof_are_not_format_errors(self):
        session = SampleSession("random")
        for reply in (OSError("host pipe failure"), None):
            with self.subTest(reply=reply), self.assertRaises(RuntimeError):
                session.decode_reply(reply)

    def test_framing_rejects_oversized_or_unterminated_lines(self):
        for text in ("x" * 65537 + "\n", '{"from":"a1","to":"a2"}'):
            session = SampleSession("random")
            session.process = SimpleNamespace(stdout=io.StringIO(text))
            session._read_replies()
            with self.subTest(length=len(text)), self.assertRaises(FormatError):
                session.decode_reply(session._replies.get_nowait())
            self.assertIsNone(session._replies.get_nowait())

    def test_invalid_utf8_is_a_format_error(self):
        session = SampleSession("random")
        session.process = SimpleNamespace(stdout=io.TextIOWrapper(io.BytesIO(bytes([255, 10])),
                                                                  encoding="utf-8"))
        try:
            session._read_replies()
            with self.assertRaisesRegex(FormatError, "UTF-8"):
                session.decode_reply(session._replies.get_nowait())
        finally:
            session.process.stdout.close()

    def test_reader_io_failure_is_a_system_error(self):
        session = SampleSession("random")
        session.process = SimpleNamespace(stdout=Mock())
        session.process.stdout.readline.side_effect = OSError("host read failure")
        session._read_replies()
        with self.assertRaisesRegex(RuntimeError, "host read failure"):
            session.decode_reply(session._replies.get_nowait())

    def test_valid_line_is_passed_through_unchanged(self):
        session = SampleSession("random")
        session.process = SimpleNamespace(stdout=io.StringIO('{"from":"a1","to":"a2"}\n'))
        session._read_replies()
        self.assertEqual(session.decode_reply(session._replies.get_nowait()),
                         {"from": "a1", "to": "a2"})
        self.assertIsNone(session._replies.get_nowait())
