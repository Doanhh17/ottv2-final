"""Check network isolation using only a temporary Ubuntu loopback listener."""

import errno
import json
import os
from pathlib import Path
import socket
import sys
import subprocess
from tempfile import TemporaryDirectory

from isolate_smoke import BASE, BOX, ISOLATE, checked

PROGRAM = """
import json, os, socket, sys
report = {'namespace': os.readlink('/proc/self/ns/net'), 'blocked': False}
try:
    with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=2):
        pass
except OSError as error:
    report.update(blocked=True, errno=error.errno)
print(json.dumps(report), flush=True)
"""


def probe_listener(listener):
    # Confirm the listener is alive, and drain the accepted control connection.
    with socket.create_connection(listener.getsockname(), timeout=2) as client:
        accepted, _ = listener.accept()
        with accepted:
            accepted.sendall(b"OK")
        if client.recv(2) != b"OK":
            raise RuntimeError("outside-sandbox control connection failed")


def check_network():
    if not sys.platform.startswith("linux"):
        raise RuntimeError("run this script inside Ubuntu with python3")
    if BOX.exists() or BOX.is_symlink():
        raise RuntimeError(f"box 0 already exists at {BOX}; refusing to reset it")
    checked([ISOLATE, "--check-config"])
    cg_root = checked([ISOLATE, "--cg", "--print-cg-root"]).stdout.strip()
    if not cg_root or not Path(cg_root).is_dir():
        raise RuntimeError("cgroup directory is missing; check isolate.service")
    host_namespace = os.readlink("/proc/self/ns/net")
    attempted_init = False
    try:
        attempted_init = True
        created = checked(BASE + ["--init"])
        if created.stdout.strip() != str(BOX):
            raise RuntimeError("unexpected box path; check the installed configuration")
        print("INIT: box 0 created", flush=True)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.settimeout(2)
            listener.bind(("127.0.0.1", 0))
            listener.listen(2)
            probe_listener(listener)
            print("CONTROL: Ubuntu can reach the test listener", flush=True)
            port = listener.getsockname()[1]
            with TemporaryDirectory(prefix="ottv2-isolate-network-") as temporary:
                meta = Path(temporary) / "run.meta"
                result = checked(BASE + ["--time=1", "--wall-time=5", "--cg-mem=262144",
                                         f"--meta={meta}", "--run", "--", "/usr/bin/python3",
                                         "-I", "-c", PROGRAM, str(port)])
                values = dict(line.split(":", 1) for line in meta.read_text().splitlines() if ":" in line)
                if values.get("exitcode") != "0" or "status" in values:
                    raise RuntimeError(f"program did not finish normally: {values}")
                report = json.loads(result.stdout)
                if report.get("namespace") == host_namespace or not report.get("namespace"):
                    raise RuntimeError("sandbox does not have a separate network namespace")
                blocked_errors = {errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH,
                                  errno.EACCES, errno.EPERM}
                if report.get("blocked") is not True or report.get("errno") not in blocked_errors:
                    raise RuntimeError(f"network isolation was not confirmed: {report}")
                print("SANDBOX: separate network namespace; test connection blocked", flush=True)
            # Rule out a dead listener as the reason the sandbox could not connect.
            probe_listener(listener)
            print("CONTROL: test listener still reachable outside sandbox", flush=True)
    finally:
        if attempted_init:
            checked(BASE + ["--cleanup"])
            if BOX.exists():
                raise RuntimeError("box directory still exists after cleanup")
            print("CLEANUP: box 0 removed", flush=True)
    print("NETWORK CHECK OK: sandbox could not reach the Ubuntu test listener.")


def main():
    try:
        check_network()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"NETWORK CHECK ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
