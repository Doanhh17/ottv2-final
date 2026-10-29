"""Verify local box CPU masks, including an attempt to widen affinity."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from isolate_smoke import ISOLATE, checked

EXPECTED = {0: 0, 1: 2}
PROGRAM = """
import json, os, sys
report = {'before': sorted(os.sched_getaffinity(0))}
try:
    os.sched_setaffinity(0, set(json.loads(sys.argv[1])))
except OSError as error:
    report['expand_errno'] = error.errno
report['after'] = sorted(os.sched_getaffinity(0))
print(json.dumps(report), flush=True)
"""


def check_box(box_id, cpu, host_cpus):
    box = Path(f"/var/local/lib/isolate/{box_id}")
    base = [ISOLATE, "--cg", f"--box-id={box_id}"]
    attempted_init = False
    try:
        attempted_init = True
        created = checked(base + ["--init"])
        if created.stdout.strip() != str(box):
            raise RuntimeError("unexpected box path; check the installed configuration")
        print(f"INIT: box {box_id} created", flush=True)
        with TemporaryDirectory(prefix="ottv2-isolate-cpu-pin-") as temporary:
            meta = Path(temporary) / "run.meta"
            result = checked(base + ["--time=1", "--wall-time=5", "--cg-mem=262144",
                                     f"--meta={meta}", "--run", "--", "/usr/bin/python3",
                                     "-I", "-c", PROGRAM, json.dumps(host_cpus)])
            values = dict(line.split(":", 1) for line in meta.read_text().splitlines() if ":" in line)
            if values.get("exitcode") != "0" or "status" in values:
                raise RuntimeError(f"program did not finish normally: {values}")
            report = json.loads(result.stdout)
            if report.get("before") != [cpu] or report.get("after") != [cpu]:
                raise RuntimeError(f"box {box_id} CPU pin not enforced: {report}")
            print(f"BOX {box_id}: before={report['before']} | after_attempt={report['after']}",
                  flush=True)
    finally:
        if attempted_init:
            checked(base + ["--cleanup"])
            if box.exists():
                raise RuntimeError(f"box {box_id} still exists after cleanup")
            print(f"CLEANUP: box {box_id} removed", flush=True)


def check_cpu_pin(expected=None):
    expected = EXPECTED if expected is None else expected
    if not sys.platform.startswith("linux"):
        raise RuntimeError("run this script inside Ubuntu with python3")
    host_cpus = sorted(os.sched_getaffinity(0))
    if len(set(expected.values())) != 2 or not set(expected.values()).issubset(host_cpus):
        raise RuntimeError(f"host CPU mask changed; review local mapping: {host_cpus}")
    for box_id in expected:
        box = Path(f"/var/local/lib/isolate/{box_id}")
        if box.exists() or box.is_symlink():
            raise RuntimeError(f"box {box_id} already exists; refusing to reset it")
    checked([ISOLATE, "--check-config"])
    cg_root = checked([ISOLATE, "--cg", "--print-cg-root"]).stdout.strip()
    if not cg_root or not Path(cg_root).is_dir():
        raise RuntimeError("cgroup directory is missing; check isolate.service")
    for box_id, cpu in expected.items():
        check_box(box_id, cpu, host_cpus)
    if sorted(os.sched_getaffinity(0)) != host_cpus:
        raise RuntimeError("host affinity unexpectedly changed")
    print("CPU PIN CHECK OK: each box stayed on its assigned logical CPU.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-a', type=int, default=EXPECTED[0])
    parser.add_argument('--cpu-b', type=int, default=EXPECTED[1])
    args = parser.parse_args()
    try:
        check_cpu_pin({0: args.cpu_a, 1: args.cpu_b})
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"CPU PIN CHECK ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
