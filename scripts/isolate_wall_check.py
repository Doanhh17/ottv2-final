"""Verify that wall time stops a sleeping program which uses little CPU."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from isolate_smoke import BASE, BOX, ISOLATE, checked

WALL_LIMIT = 0.5
CPU_LIMIT = 2


def check_wall():
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
        print("TEST: sleep for 10s | CPU limit=2s | wall limit=0.5s", flush=True)
        with TemporaryDirectory(prefix="ottv2-isolate-wall-") as temporary:
            meta = Path(temporary) / "run.meta"
            result = subprocess.run(
                BASE + [f"--time={CPU_LIMIT}", f"--wall-time={WALL_LIMIT}", "--cg-mem=262144",
                        f"--meta={meta}", "--run", "--", "/usr/bin/python3", "-I", "-c",
                        "import time; time.sleep(10)"],
                capture_output=True, text=True, timeout=15,
            )
            if not meta.is_file():
                raise RuntimeError(f"missing metadata: {result.stderr.strip()}")
            values = dict(line.split(":", 1) for line in meta.read_text().splitlines() if ":" in line)
            cpu = float(values.get("time", "nan"))
            wall = float(values.get("time-wall", "nan"))
            if (result.returncode != 1 or values.get("status") != "TO"
                    or values.get("message") != "Time limit exceeded (wall clock)"
                    or not 0 <= cpu < CPU_LIMIT or not wall >= WALL_LIMIT):
                raise RuntimeError(
                    f"wall limit was not confirmed: exit={result.returncode}, meta={values}, "
                    f"stderr={result.stderr.strip()}"
                )
            print(f"META: status=TO | cpu={cpu:.3f}s | wall={wall:.3f}s", flush=True)
            print("EXPECTED: Time limit exceeded (wall clock)", flush=True)
    finally:
        if attempted_init:
            checked(BASE + ["--cleanup"])
            if BOX.exists():
                raise RuntimeError("box directory still exists after cleanup")
            print("CLEANUP: box 0 removed", flush=True)
    print("WALL CHECK OK: Isolate stopped the sleeping program at the wall limit.")


def main():
    try:
        check_wall()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"WALL CHECK ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
