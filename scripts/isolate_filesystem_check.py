"""Test access to disposable files inside and outside an Isolate box."""

import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from isolate_smoke import BASE, BOX, ISOLATE, checked

MARKER = "OTTv2 disposable host marker"
PROGRAM = """
import json, os, sys
from pathlib import Path
outside = Path(sys.argv[1])
report = {'namespace': os.readlink('/proc/self/ns/mnt')}
try:
    outside.read_text()
    report['read_blocked'] = False
except OSError as error:
    report.update(read_blocked=True, read_errno=error.errno)
try:
    outside.write_text('UNEXPECTED CHANGE')
    report['write_blocked'] = False
except OSError as error:
    report.update(write_blocked=True, write_errno=error.errno)
inside = Path('/box/probe.txt')
inside.write_text('BOX WRITE OK')
report['inside'] = inside.read_text()
print(json.dumps(report), flush=True)
"""


def check_filesystem():
    if not sys.platform.startswith("linux"):
        raise RuntimeError("run this script inside Ubuntu with python3")
    if BOX.exists() or BOX.is_symlink():
        raise RuntimeError(f"box 0 already exists at {BOX}; refusing to reset it")
    checked([ISOLATE, "--check-config"])
    cg_root = checked([ISOLATE, "--cg", "--print-cg-root"]).stdout.strip()
    if not cg_root or not Path(cg_root).is_dir():
        raise RuntimeError("cgroup directory is missing; check isolate.service")
    host_namespace = os.readlink("/proc/self/ns/mnt")
    attempted_init = False
    try:
        attempted_init = True
        created = checked(BASE + ["--init"])
        if created.stdout.strip() != str(BOX):
            raise RuntimeError("unexpected box path; check the installed configuration")
        print("INIT: box 0 created", flush=True)
        # Only this throwaway directory is the target of outside access attempts.
        with TemporaryDirectory(prefix="ottv2-isolate-files-", dir=Path.home()) as temporary:
            marker = Path(temporary) / "host-marker.txt"
            marker.write_text(MARKER)
            if marker.read_text() != MARKER:
                raise RuntimeError("host file control failed")
            print("CONTROL: Ubuntu can read/write the disposable host file", flush=True)
            meta = Path(temporary) / "run.meta"
            result = checked(BASE + ["--time=1", "--wall-time=5", "--cg-mem=262144",
                                     f"--meta={meta}", "--run", "--", "/usr/bin/python3",
                                     "-I", "-c", PROGRAM, str(marker)])
            values = dict(line.split(":", 1) for line in meta.read_text().splitlines() if ":" in line)
            if values.get("exitcode") != "0" or "status" in values:
                raise RuntimeError(f"program did not finish normally: {values}")
            report = json.loads(result.stdout)
            if not report.get("namespace") or report["namespace"] == host_namespace:
                raise RuntimeError("sandbox does not have a separate mount namespace")
            blocked_errors = {errno.ENOENT, errno.EACCES, errno.EPERM, errno.EROFS}
            for operation in ("read", "write"):
                if (report.get(f"{operation}_blocked") is not True
                        or report.get(f"{operation}_errno") not in blocked_errors):
                    raise RuntimeError(f"outside file {operation} was not blocked: {report}")
            if marker.read_text() != MARKER:
                raise RuntimeError("outside host file was changed")
            if (report.get("inside") != "BOX WRITE OK"
                    or (BOX / "box" / "probe.txt").read_text() != "BOX WRITE OK"):
                raise RuntimeError("write inside /box was not confirmed")
            print("SANDBOX: outside file read/write blocked; host file unchanged", flush=True)
            print("SANDBOX: /box/probe.txt write/read succeeded", flush=True)
    finally:
        if attempted_init:
            checked(BASE + ["--cleanup"])
            if BOX.exists():
                raise RuntimeError("box directory still exists after cleanup")
            print("CLEANUP: box 0 removed", flush=True)
    print("FILESYSTEM CHECK OK: private host file protected; box workspace writable.")


def main():
    try:
        check_filesystem()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"FILESYSTEM CHECK ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
