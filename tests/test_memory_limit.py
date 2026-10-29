from collections import deque
from types import SimpleNamespace
import unittest

from ottv2.isolated_session import IsolatedSampleSession, parse_memory_events


class MemoryLimitTests(unittest.TestCase):
    def test_memory_events_keep_kernel_counts(self):
        self.assertEqual(parse_memory_events("low 0\nhigh 0\nmax 30\noom 2\noom_kill 1\n"),
                         {"low": 0, "high": 0, "max": 30, "oom": 2, "oom_kill": 1})

    def test_missing_duplicate_negative_or_malformed_counters_are_system_errors(self):
        for text in ("", "max 1\noom 1\n", "max 1\nmax 2\noom 1\noom_kill 1\n",
                     "max -1\noom 1\noom_kill 1\n", "max bad\noom 1\noom_kill 1\n",
                     "max 1 extra\noom 1\noom_kill 1\n"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                parse_memory_events(text)

    def test_oom_requires_monitor_metadata_and_local_limit_evidence(self):
        session = IsolatedSampleSession("random", 0)
        session.process = SimpleNamespace(returncode=1)
        session.metadata = {"status": "SG", "exitsig": "9", "cg-oom-killed": "1"}
        session.memory_events = {"max": 30, "oom": 2, "oom_kill": 1}
        self.assertIn("limit=64 MiB", session.memory_limit_detail())

    def test_sigkill_memory_peak_or_stderr_alone_is_not_oom_proof(self):
        session = IsolatedSampleSession("random", 0)
        session.process = SimpleNamespace(returncode=1)
        session.metadata = {"status": "SG", "exitsig": "9", "cg-mem": "131072"}
        session.memory_events = {"max": 0, "oom": 0, "oom_kill": 0}
        session._errors = deque(["MemoryError: fake OOM\ncg-oom-killed:1"])
        self.assertIsNone(session.memory_limit_detail())
        self.assertIn("signal=9", session.runtime_error_detail("stopped"))

    def test_inconsistent_oom_evidence_remains_a_system_error(self):
        good = {"status": "SG", "exitsig": "9", "cg-oom-killed": "1"}
        events = {"max": 30, "oom": 2, "oom_kill": 1}
        for meta, counts, returncode in (
            (good, {}, 1),
            (good, {**events, "max": 0}, 1),
            (good, {**events, "oom": 0}, 1),
            (good, {**events, "oom_kill": 0}, 1),
            ({**good, "status": "XX"}, events, 2),
            ({**good, "exitsig": "15"}, events, 1),
            ({**good, "cg-oom-killed": "bad"}, events, 1),
        ):
            with self.subTest(meta=meta, counts=counts, returncode=returncode):
                session = IsolatedSampleSession("random", 0)
                session.process = SimpleNamespace(returncode=returncode)
                session.metadata, session.memory_events = meta, counts
                with self.assertRaises(RuntimeError):
                    session.memory_limit_detail()
