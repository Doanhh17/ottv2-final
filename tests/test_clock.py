import unittest

from ottv2.clock import combined_seconds_left, display_seconds_left


class DisplayClockTests(unittest.TestCase):
    def test_combined_clock_uses_lower_remainder_without_double_charging(self):
        for cpu, wall, expected in ((0, 0, 30), (0, 2, 28), (0.1, 0.1, 27),
                                    (0.1, 5, 25), (0.5, 15, 15), (1, 1, 0), (0, 30, 0)):
            with self.subTest(cpu=cpu, wall=wall):
                self.assertAlmostEqual(combined_seconds_left(cpu, wall), expected)
        with self.assertRaises(ValueError):
            combined_seconds_left(0, -1)

    def test_cpu_budgets_map_to_thirty_display_seconds(self):
        for cpu, expected in ((0, 30), (0.5, 27), (1, 24), (2.5, 15), (5, 0)):
            with self.subTest(cpu=cpu):
                self.assertAlmostEqual(display_seconds_left(cpu), expected)
        for cpu, expected in ((0, 30), (0.1, 27), (0.5, 15), (1, 0), (1.1, 0)):
            with self.subTest(trial_cpu=cpu):
                self.assertAlmostEqual(display_seconds_left(cpu, cpu_budget=1), expected)

    def test_direct_one_and_a_half_second_cpu_clock(self):
        for cpu, expected in ((0, 1.5), (0.1, 1.4), (1, 0.5), (1.5, 0), (2, 0)):
            with self.subTest(cpu=cpu):
                self.assertAlmostEqual(display_seconds_left(cpu, 1.5, 1.5), expected)

    def test_clock_stays_at_zero_after_cpu_budget_is_exceeded(self):
        for cpu in (5.000001, 6, 100):
            self.assertEqual(display_seconds_left(cpu), 0)

    def test_invalid_clock_inputs_are_rejected(self):
        cases = [dict(cpu_used=value) for value in (-1, float("nan"), float("inf"), True, "1", None)]
        cases += [dict(cpu_used=0, cpu_budget=value) for value in (0, -1, float("nan"))]
        cases += [dict(cpu_used=0, display_budget=value) for value in (0, -1, float("inf"))]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                display_seconds_left(**values)


if __name__ == "__main__":
    unittest.main()
