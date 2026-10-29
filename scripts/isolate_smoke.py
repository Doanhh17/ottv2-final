"""Run a trusted hello program in a fresh Isolate box, then clean it up."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ISOLATE = "/usr/local/bin/isolate"
BOX = Path("/var/local/lib/isolate/0")
BASE = [ISOLATE, "--cg", "--box-id=0"]


def checked(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"exit={result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
    return result


def smoke():
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
        with TemporaryDirectory(prefix="ottv2-isolate-smoke-") as temporary:
            meta = Path(temporary) / "run.meta"
            result = checked(BASE + ["--time=1", "--wall-time=5", "--cg-mem=262144",
                                     f"--meta={meta}", "--run", "--", "/usr/bin/python3",
                                     "-I", "-c", "print('SANDBOX OK')"])
            if result.stdout.strip() != "SANDBOX OK":
                raise RuntimeError(f"unexpected program output: {result.stdout!r}")
            values = dict(line.split(":", 1) for line in meta.read_text().splitlines() if ":" in line)
            if values.get("exitcode") != "0" or "status" in values:
                raise RuntimeError(f"sandbox did not report success: {values}")
            print(result.stdout.strip(), flush=True)
            print(f"META: exitcode=0 | cpu={values.get('time', '?')}s "
                  f"| wall={values.get('time-wall', '?')}s", flush=True)
    finally:
        # Only this script's newly initialized box is eligible for cleanup.
        if attempted_init:
            checked(BASE + ["--cleanup"])
            if BOX.exists():
                raise RuntimeError("box directory still exists after cleanup")
            print("CLEANUP: box 0 removed", flush=True)
    print("SMOKE OK: Python ran inside Isolate.")


def main():
    try:
        smoke()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"SMOKE ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
