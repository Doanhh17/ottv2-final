from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ottv2.cpu_usage import CgroupCpuMeter, parse_usage_usec


class CpuUsageTests(unittest.TestCase):
    def test_parse_usage_ignores_other_cpu_fields(self):
        self.assertEqual(parse_usage_usec("usage_usec 123456\nuser_usec 100000\nsystem_usec 23456\n"),
                         123456)

    def test_missing_duplicate_negative_and_invalid_usage_are_rejected(self):
        for value in ("user_usec 10\n", "usage_usec 1\nusage_usec 2\n", "usage_usec -1\n",
                      "usage_usec abc\n", "usage_usec 1 extra\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_usage_usec(value)

    def test_ready_baseline_excludes_startup_and_retains_all_later_cpu(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "cpu.stat"
            path.write_text("usage_usec 80000\n")
            meter = CgroupCpuMeter(path)
            self.assertEqual(meter.seconds(), 0)
            path.write_text("usage_usec 90000\n")
            self.assertAlmostEqual(meter.seconds(), 0.01)
            # No turn-specific reset: CPU accrued while waiting must still count.
            path.write_text("usage_usec 115000\n")
            self.assertAlmostEqual(meter.seconds(), 0.035)
            self.assertEqual(meter.baseline_usec, 80000)

    def test_decreasing_counter_is_rejected(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "cpu.stat"
            path.write_text("usage_usec 100\n")
            meter = CgroupCpuMeter(path)
            path.write_text("usage_usec 200\n")
            meter.seconds()
            path.write_text("usage_usec 150\n")
            with self.assertRaisesRegex(RuntimeError, "moved backwards"):
                meter.seconds()


if __name__ == "__main__":
    unittest.main()
