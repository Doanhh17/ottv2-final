"""Verify a small cgroup RAM limit with a bounded, trusted allocation test."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from isolate_smoke import BASE, BOX, ISOLATE, checked

MEMORY_KIB = 65536
PROGRAM = """
blocks = []
for _ in range(32):
    blocks.append(bytearray(b'x' * (4 * 1024 * 1024)))
print('UNEXPECTED: allocated 128 MiB', flush=True)
"""


def check_memory():
    if not sys.platform.startswith("linux"):
        raise RuntimeError("run this script inside Ubuntu with python3")
    if BOX.exists() or BOX.is_symlink():
        raise RuntimeError(f"box 0 already exists at {BOX}; refusing to reset it")
    checked([ISOLATE, "--check-config"])
    cg_root = checked([ISOLATE, "--cg", "--print-cg-root"]).stdout.strip()
    if not cg_root or not Path(cg_root).is_dir():
        raise RuntimeError("cgroup directory is missing; check isolate.service")
    attempted_init = False
    try:
        attempted_init = True
        created = checked(BASE + ["--init"])
        if created.stdout.strip() != str(BOX):
            raise RuntimeError("unexpected box path; check the installed configuration")
        print("INIT: box 0 created", flush=True)
        print("TEST: allocate beyond 64 MiB | CPU limit=2s | wall limit=5s", flush=True)
        with TemporaryDirectory(prefix="ottv2-isolate-memory-") as temporary:
            meta = Path(temporary) / "run.meta"
            result = subprocess.run(
                BASE + ["--time=2", "--wall-time=5", f"--cg-mem={MEMORY_KIB}",
                        f"--meta={meta}", "--run", "--", "/usr/bin/python3", "-I", "-c", PROGRAM],
                capture_output=True, text=True, timeout=15,
            )
            if not meta.is_file():
                raise RuntimeError(f"missing metadata: {result.stderr.strip()}")
            values = dict(line.split(":", 1) for line in meta.read_text().splitlines() if ":" in line)
            # SIGKILL alone is not proof of memory exhaustion: require the cgroup OOM marker.
            if (result.returncode != 1 or values.get("cg-oom-killed") != "1"
                    or values.get("status") != "SG" or values.get("exitsig") != "9"):
                raise RuntimeError(
                    f"RAM limit was not confirmed: exit={result.returncode}, meta={values}, "
                    f"stderr={result.stderr.strip()}"
                )
            print(f"META: status=SG | oom_killed=1 | memory={values.get('cg-mem', '?')} KiB",
                  flush=True)
            print("EXPECTED: sandbox program killed after exceeding RAM limit", flush=True)
    finally:
        if attempted_init:
            checked(BASE + ["--cleanup"])
            if BOX.exists():
                raise RuntimeError("box directory still exists after cleanup")
            print("CLEANUP: box 0 removed", flush=True)
    print("RAM CHECK OK: the cgroup stopped the allocation test.")


def main():
    try:
        check_memory()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"RAM CHECK ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
