"""Persistent Isolate sessions for the two bundled bots, with demo limits."""

from pathlib import Path
import json
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from .cpu_usage import CgroupCpuMeter
from .sample_session import ROOT, BotStartupError, SampleSession
from .rules import MEMORY_LIMIT_MIB

ISOLATE = "/usr/local/bin/isolate"
BOX_ROOT = Path("/var/local/lib/isolate")
MAX_BOX_ID = 25  # 0/1 for legacy matches and READY; 2..25 for 12 pool slots.


def validate_box_pair(box_ids):
    if (not isinstance(box_ids, (tuple, list)) or len(box_ids) != 2
            or any(type(i) is not int or not 0 <= i <= MAX_BOX_ID for i in box_ids)
            or box_ids[0] % 2 or box_ids[1] != box_ids[0] + 1):
        raise ValueError('sandbox pair must be consecutive even/odd IDs in 0..25')
    return tuple(box_ids)


def parse_memory_events(text):
    counters = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] in counters:
            raise RuntimeError("invalid or duplicate cgroup memory event")
        try:
            value = int(fields[1])
        except ValueError as error:
            raise RuntimeError("invalid cgroup memory event count") from error
        if value < 0:
            raise RuntimeError("negative cgroup memory event count")
        counters[fields[0]] = value
    if not {"max", "oom", "oom_kill"} <= counters.keys():
        raise RuntimeError("missing required cgroup memory events")
    return counters


def checked(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}")
    return result


class IsolatedSampleSession(SampleSession):
    def __init__(self, strategy, box_id, seed=1):
        super().__init__(strategy, seed)
        if type(box_id) is not int or not 0 <= box_id <= MAX_BOX_ID:
            raise ValueError('sandbox ID must be in 0..25')
        self.box_id = box_id
        self.box = BOX_ROOT / str(box_id)
        self.base = [ISOLATE, "--cg", f"--box-id={box_id}"]
        self.metadata = {}
        self.memory_events = {}
        self.whole_cpu_limit = 2
        self.whole_wall_limit = 10
        self.cpu_meter = None
        self.cpu_after_ready_seconds = 0.0
        self._temporary = None
        self._attempted_init = False
        self._cgroup = None

    def __enter__(self):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("run this demo inside Ubuntu with python3")
        if self.box.exists() or self.box.is_symlink():
            raise RuntimeError(f"box {self.box_id} already exists; refusing to reset it")
        checked([ISOLATE, "--check-config"])
        cg_root = checked([ISOLATE, "--cg", "--print-cg-root"]).stdout.strip()
        if not cg_root or not Path(cg_root).is_dir():
            raise RuntimeError("cgroup directory is missing; check isolate.service")
        self._temporary = TemporaryDirectory(prefix=f"ottv2-bot-{self.box_id}-")
        self._meta = Path(self._temporary.name) / "run.meta"
        try:
            self._attempted_init = True
            created = checked(self.base + ["--init"])
            if created.stdout.strip() != str(self.box):
                raise RuntimeError("unexpected box path; check the installed configuration")
            self._cgroup = Path(cg_root) / f"box-{self.box_id}"
            self._prepare_files()
            super().__enter__()
            # Snapshot as soon as the engine has received and validated READY.
            self.cpu_meter = CgroupCpuMeter(Path(cg_root) / f"box-{self.box_id}" / "cpu.stat")
            return self
        except BaseException as error:
            try:
                self.close()
                if isinstance(error, BotStartupError):
                    self._collect_outcome()
            finally:
                self._cleanup()
            raise

    def _prepare_files(self):
        package = self.box / "box" / "ottv2"
        package.mkdir()
        for name in ("__init__.py", "engine.py", "protocol.py", "bot_process.py", "search_bot.py", "advanced_bot.py"):
            shutil.copyfile(ROOT / "ottv2" / name, package / name)

    def _bot_arguments(self):
        bootstrap = ("import runpy, sys; sys.path.insert(0, '/box'); "
                     "runpy.run_module('ottv2.bot_process', run_name='__main__')")
        return ["/usr/bin/python3", "-I", "-B", "-u", "-c", bootstrap,
                "--strategy", self.strategy, "--seed", str(self.seed)]

    def _start_process(self):
        # These are whole-session demo guards, including Python startup.
        command = self.base + [f"--time={self.whole_cpu_limit}", f"--wall-time={self.whole_wall_limit}",
                               f"--cg-mem={MEMORY_LIMIT_MIB * 1024}",
                               "--chdir=/box", f"--meta={self._meta}", "--run", "--",
                               *self._bot_arguments()]
        return subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, cwd=ROOT)

    def _stop_process(self):
        # Let Isolate terminate its child and record metadata before cleanup.
        self.process.terminate()
        self.process.wait(timeout=self.whole_wall_limit + 2)

    def sample_cpu_seconds(self):
        if self.cpu_meter is None:
            raise RuntimeError("CPU baseline is not available before READY")
        self.cpu_after_ready_seconds = self.cpu_meter.seconds()
        return self.cpu_after_ready_seconds

    def runtime_error_detail(self, detail):
        """Confirm a runtime candidate after close has collected Isolate metadata."""
        meta = self.metadata
        status = meta.get("status")
        # Missing/internal/resource-limit metadata is not evidence of a bot crash.
        # The caller checks memory_limit_detail first; whole-session TO is separate.
        if (self.process.returncode != (0 if status is None else 1) or not meta
                or meta.get("cg-oom-killed", "0") != "0"
                or status not in (None, "RE", "SG")):
            raise RuntimeError(f"cannot classify bot runtime failure: {meta}")
        if status is None and meta.get("exitcode") != "0":
            raise RuntimeError(f"missing normal bot exit status: {meta}")
        if status == "RE" and (not meta.get("exitcode", "").isdigit() or int(meta["exitcode"]) == 0):
            raise RuntimeError(f"missing bot error exit status: {meta}")
        if status == "SG" and (not meta.get("exitsig", "").isdigit() or int(meta["exitsig"]) == 0):
            raise RuntimeError(f"missing bot signal status: {meta}")
        outcome = (f"signal={meta['exitsig']}" if status == "SG"
                   else f"exitcode={meta['exitcode']}")
        stderr = "".join(self._errors)[-2048:].strip()
        return f"{detail} | {outcome} | stderr={json.dumps(stderr, ensure_ascii=True)}"

    def memory_limit_detail(self):
        """Require both Isolate OOM metadata and this sandbox's limit events."""
        marker = self.metadata.get("cg-oom-killed", "0")
        if marker == "0":
            return None
        if (marker != "1" or self.process.returncode != 1
                or self.metadata.get("status") != "SG" or self.metadata.get("exitsig") != "9"
                or any(self.memory_events.get(key, 0) <= 0 for key in ("max", "oom", "oom_kill"))):
            raise RuntimeError(f"cannot confirm sandbox memory limit: meta={self.metadata}, "
                               f"events={self.memory_events}")
        return (f"limit={MEMORY_LIMIT_MIB} MiB | cgroup_oom_killed=1 | signal=9 | "
                f"max_events={self.memory_events['max']} | oom_events={self.memory_events['oom']}")

    def startup_error_detail(self, error):
        memory = self.memory_limit_detail()
        if memory is not None:
            return "startup_memory_limit", memory
        return error.reason, self.runtime_error_detail(error.detail)

    def _collect_outcome(self):
        if self._meta.is_file():
            self.metadata = dict(line.split(":", 1) for line in self._meta.read_text().splitlines()
                                 if ":" in line)
        if self._cgroup is not None:
            self.memory_events = parse_memory_events((self._cgroup / "memory.events").read_text())

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            try:
                if self.cpu_meter is not None:
                    self.sample_cpu_seconds()
            finally:
                # Even a counter/read failure must not leave a bot running.
                super().__exit__(exc_type, exc_value, traceback)
            # Capture before --cleanup removes this sandbox's cgroup.
            self._collect_outcome()
            if exc_type is None and (self.process.returncode != 0
                                     or self.metadata.get("exitcode") != "0" or "status" in self.metadata):
                raise RuntimeError(f"box {self.box_id} bot did not exit normally: {self.metadata}")
        finally:
            self._cleanup()

    def _cleanup(self):
        try:
            if self._attempted_init:
                checked(self.base + ["--cleanup"])
                if self.box.exists():
                    raise RuntimeError(f"box {self.box_id} still exists after cleanup")
                self._attempted_init = False
        finally:
            if self._temporary is not None:
                self._temporary.cleanup()
