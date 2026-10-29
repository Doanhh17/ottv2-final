"""Convert measured CPU into a display clock; this does not enforce a limit."""

import math

CPU_BUDGET_SECONDS = 5.0
DISPLAY_BUDGET_SECONDS = 30.0


def display_seconds_left(cpu_used, cpu_budget=CPU_BUDGET_SECONDS,
                         display_budget=DISPLAY_BUDGET_SECONDS):
    for value in (cpu_used, cpu_budget, display_budget):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("clock inputs must be finite numbers")
    if cpu_used < 0 or cpu_budget <= 0 or display_budget <= 0:
        raise ValueError("CPU used must be nonnegative and clock budgets must be positive")
    if cpu_used >= cpu_budget:
        return 0.0
    return display_budget * (1 - cpu_used / cpu_budget)


def combined_seconds_left(cpu_used, wall_used, cpu_budget=1.0, wall_budget=30.0):
    """Use the lower of the CPU conversion and the own-turn wall clock."""
    return min(display_seconds_left(cpu_used, cpu_budget),
               display_seconds_left(wall_used, wall_budget))
