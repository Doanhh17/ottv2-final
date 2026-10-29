"""Read cumulative cgroup CPU usage and subtract the engine's READY snapshot."""

from pathlib import Path


def parse_usage_usec(text):
    values = []
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == "usage_usec":
            if len(fields) != 2:
                raise ValueError("invalid usage_usec in cpu.stat")
            try:
                value = int(fields[1])
            except ValueError as error:
                raise ValueError("invalid usage_usec in cpu.stat") from error
            if value < 0:
                raise ValueError("negative CPU usage in cpu.stat")
            values.append(value)
    if len(values) != 1:
        raise ValueError("cpu.stat must contain exactly one usage_usec")
    return values[0]


class CgroupCpuMeter:
    def __init__(self, stat_file):
        self.stat_file = Path(stat_file)
        self.baseline_usec = self._read()
        self.last_usec = self.baseline_usec

    def _read(self):
        return parse_usage_usec(self.stat_file.read_text())

    def seconds(self):
        current = self._read()
        if current < self.last_usec:
            raise RuntimeError("cgroup CPU counter moved backwards; refuse an invalid measurement")
        self.last_usec = current
        return (current - self.baseline_usec) / 1_000_000
