from copy import deepcopy
import json
import subprocess
import sys
import unittest

from ottv2.engine import sample_game
from ottv2.protocol import state_message, validate_ready, validate_time_info
from ottv2.sample_session import ROOT


def sample_time():
    return {"cpu_budget_seconds": 1.5, "cpu_remaining_seconds": {"A": 1.25, "B": 1.5},
            "response_limit_seconds": 1.0, "response_remaining_seconds": 0.9}


class ProtocolTimeTests(unittest.TestCase):
    def test_legacy_messages_can_still_omit_clocks(self):
        message = state_message(sample_game())
        self.assertEqual(message["protocol_version"], 1)
        self.assertNotIn("time", message)

    def test_clock_snapshot_is_serializable_and_not_aliased(self):
        timing = sample_time()
        message = state_message(sample_game(), timing)
        self.assertEqual(json.loads(json.dumps(message))["time"], timing)
        timing["cpu_remaining_seconds"]["A"] = 0
        self.assertEqual(message["time"]["cpu_remaining_seconds"]["A"], 1.25)
        self.assertEqual(message["board"], state_message(sample_game())["board"])

    def test_invalid_times_are_rejected(self):
        cases = [None, {}, "1.5"]
        for key, values in (
            ("cpu_budget_seconds", (0, -1, True, float("inf"))),
            ("response_limit_seconds", (0, "1", float("nan"))),
            ("response_remaining_seconds", (-1, 1.1, True, float("nan"))),
            ("cpu_remaining_seconds", ({"A": 1}, {"A": -1, "B": 1},
                                       {"A": 1.6, "B": 1}, {"A": True, "B": 1},
                                       {"A": float("inf"), "B": 1})),
        ):
            for value in values:
                timing = sample_time()
                timing[key] = value
                cases.append(timing)
        for timing in cases:
            with self.subTest(timing=timing), self.assertRaises(ValueError):
                validate_time_info(timing)

    def test_bot_process_reads_valid_time_and_rejects_invalid_time(self):
        game = sample_game()
        good = state_message(game, sample_time())
        bad = deepcopy(good)
        bad["time"]["response_remaining_seconds"] = -0.1
        for message, valid in ((good, True), (bad, False)):
            with self.subTest(valid=valid):
                result = subprocess.run(
                    [sys.executable, "-m", "ottv2.bot_process", "--strategy", "random"],
                    input=json.dumps(message) + "\n", capture_output=True, text=True,
                    timeout=5, cwd=ROOT)
                lines = result.stdout.splitlines()
                validate_ready(json.loads(lines[0]))
                if valid:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(len(lines), 2)
                    game.validate_move(json.loads(lines[1]))
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(lines), 1)
                    self.assertIn("BOT INPUT ERROR: time response", result.stderr)
