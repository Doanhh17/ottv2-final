"""Verify CPU exhaustion with a trusted busy loop; never run submitted bots."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from isolate_smoke import BASE, BOX, ISOLATE, checked

CPU_LIMIT = 0.5


def check_cpu():
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
        print("TEST: busy loop | CPU limit=0.5s | wall limit=5s", flush=True)
        with TemporaryDirectory(prefix="ottv2-isolate-cpu-") as temporary:
            meta = Path(temporary) / "run.meta"
            # Exit code 1 from Isolate is expected for a program exceeding a limit.
            result = subprocess.run(
                BASE + [f"--time={CPU_LIMIT}", "--wall-time=5", "--cg-mem=262144",
                        f"--meta={meta}", "--run", "--", "/usr/bin/python3", "-I", "-c",
                        "while True: pass"],
                capture_output=True, text=True, timeout=15,
            )
            if not meta.is_file():
                raise RuntimeError(f"missing metadata: {result.stderr.strip()}")
            values = dict(line.split(":", 1) for line in meta.read_text().splitlines() if ":" in line)
            cpu = float(values.get("time", "nan"))
            if (result.returncode != 1 or values.get("status") != "TO"
                    or values.get("message") != "Time limit exceeded"
                    or not cpu >= CPU_LIMIT):
                raise RuntimeError(
                    f"CPU limit was not confirmed: exit={result.returncode}, meta={values}, "
                    f"stderr={result.stderr.strip()}"
                )
            print(f"META: status=TO | cpu={cpu:.3f}s | wall={values.get('time-wall', '?')}s",
                  flush=True)
            print("EXPECTED: Time limit exceeded (CPU)", flush=True)
    finally:
        if attempted_init:
            checked(BASE + ["--cleanup"])
            if BOX.exists():
                raise RuntimeError("box directory still exists after cleanup")
            print("CLEANUP: box 0 removed", flush=True)
    print("CPU CHECK OK: Isolate stopped the busy loop at the CPU limit.")


def main():
    try:
        check_cpu()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"CPU CHECK ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
