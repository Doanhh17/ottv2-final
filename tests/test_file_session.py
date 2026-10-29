import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ottv2.cpu_trial import run_trial
from ottv2.engine import FormatError
from ottv2.file_session import IsolatedFileSession
from ottv2.isolated_session import BOX_ROOT
from ottv2.sample_session import ROOT

READY = 'import json,sys\nprint(json.dumps({"protocol_version":1,"type":"ready"}),flush=True)\n'


class FileInputTests(unittest.TestCase):
    def test_snapshot_is_not_executed_or_changed_by_later_file_edit(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.py"
            source = b'raise RuntimeError("must not run on host")\n'
            path.write_bytes(source)
            session = IsolatedFileSession(path, 0)
            path.write_text('different source')
            self.assertEqual(session.source, source)
            self.assertEqual(session.source_info["sha256"], hashlib.sha256(source).hexdigest())
            self.assertIsNone(session.process)
            self.assertEqual(session._bot_arguments()[-1], "/box/main.py")

    def test_missing_empty_wrong_name_and_oversized_files_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.py"
            for data in (None, b'', b'x' * (5 * 1024 * 1024 + 1)):
                if data is not None:
                    path.write_bytes(data)
                with self.assertRaises(ValueError):
                    IsolatedFileSession(path, 0)
            other = Path(tmp) / "other.py"
            other.write_text('pass')
            with self.assertRaises(ValueError):
                IsolatedFileSession(other, 0)

    def test_pending_stdout_is_bounded_and_fault_is_visible(self):
        session = IsolatedFileSession(ROOT / "examples/greedy/main.py", 0)
        for _ in range(100):
            session._publish('{}\n')
        self.assertEqual(session._replies.qsize(), 16)
        with self.assertRaisesRegex(FormatError, "pending-response"):
            session.decode_reply('{}\n')

    def test_invalid_second_file_prevents_any_sandbox_start(self):
        with patch("ottv2.cpu_trial.sys.platform", "linux"), patch("ottv2.isolated_session.checked") as command:
            with self.assertRaises(ValueError):
                run_trial(bot_a_file=ROOT / "examples/greedy/main.py", bot_b_file="missing/main.py")
            command.assert_not_called()


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/local/bin/isolate").is_file(),
                     "requires configured Isolate")
class FileBotIntegrationTests(unittest.TestCase):
    def assert_clean(self):
        self.assertFalse(any((BOX_ROOT / str(i)).exists() for i in (0, 1)))

    def test_standalone_bot_completes_with_versioned_source(self):
        with TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            source = ROOT / "examples/greedy/main.py"
            game, _, _ = run_trial(bot_a_file=source, bot_b="guardian", result_file=result, emit=lambda _: None)
            record = json.loads(result.read_text())
            self.assertIsNotNone(game.result)
            self.assertEqual(record["bots"], {"A": "file", "B": "guardian"})
            self.assertEqual(record["bot_sources"]["A"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(record["limits"]["memory_limit_mib_per_bot"], 64)
            self.assertEqual(record["illegal_attempts"], {"A": 0, "B": 0})
        self.assert_clean()

    def test_both_sides_can_use_standalone_files(self):
        game, _, _ = run_trial(bot_a_file=ROOT / "examples/greedy/main.py",
                               bot_b_file=ROOT / "examples/greedy/main.py", emit=lambda _: None)
        self.assertIsNotNone(game.result)
        self.assert_clean()

    def test_neighbor_module_is_not_included_with_main_py(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.py"
            path.write_text('import ottv2_private_helper_for_test\n' + READY)
            (Path(tmp) / "ottv2_private_helper_for_test.py").write_text('pass\n')
            game, _, _ = run_trial(bot_a_file=path, emit=lambda _: None)
            self.assertEqual((game.result, game.reason, len(game.history)), ("B", "startup_runtime_error", 0))
        self.assert_clean()

    def test_bot_faults_use_existing_adjudication_and_cleanup(self):
        cases = [(b'def broken(:', "startup_runtime_error"),
                 (b'print("{}",flush=True)', "startup_format_error"),
                 ((READY + 'for line in sys.stdin:\n print("broken",flush=True)\n').encode(), "format_error"),
                 ((READY + 'for line in sys.stdin:\n raise RuntimeError("bot crash")\n').encode(), "runtime_error"),
                 ((READY + 'import time\nfor line in sys.stdin:\n time.sleep(10)\n').encode(), "response_timeout")]
        for source, reason in cases:
            with self.subTest(reason=reason), TemporaryDirectory() as tmp:
                path = Path(tmp) / "main.py"
                path.write_bytes(source)
                game, _, _ = run_trial(bot_a_file=path, response_timeout=0.2, emit=lambda _: None)
                self.assertEqual((game.result, game.reason, len(game.history)), ("B", reason, 0))
                self.assert_clean()


if __name__ == "__main__":
    unittest.main()
