"""Local single-file main.py trial, using the existing Isolate lifecycle."""

import hashlib
from pathlib import Path
from queue import Full, Queue

from .isolated_session import IsolatedSampleSession
from .engine import FormatError
from .sample_session import BotProcessError

MAX_SOURCE_BYTES = 5 * 1024 * 1024


class IsolatedFileSession(IsolatedSampleSession):
    def __init__(self, path, box_id, seed=1):
        path = Path(path)
        if path.name != "main.py" or path.is_symlink() or not path.is_file():
            raise ValueError("bot file must be a regular file named main.py, not a link")
        with path.open("rb") as stream:
            source = stream.read(MAX_SOURCE_BYTES + 1)
        if not source or len(source) > MAX_SOURCE_BYTES:
            raise ValueError("main.py must contain 1..5242880 bytes")
        self._initialize_source(source, box_id, seed)

    def _initialize_source(self, source, box_id, seed):
        # Snapshot only. Never import or execute submitted code on the host.
        self.source = source
        self.source_info = {"entrypoint": "main.py", "sha256": hashlib.sha256(source).hexdigest()}
        super().__init__("random", box_id, seed)
        self.strategy = "file"
        self._replies = Queue(maxsize=16)
        self._output_fault = None

    def _publish(self, value):
        try:
            self._replies.put_nowait(value)
            return True
        except Full:
            self._output_fault = "main.py exceeded the pending-response limit"
            return False

    def _read_replies(self):
        try:
            while line := self.process.stdout.readline(65537):
                if len(line) > 65536 or not line.endswith("\n"):
                    self._publish(FormatError("bot response needs one JSON line, max 65536 characters"))
                    return
                if not self._publish(line):
                    return
        except UnicodeError:
            self._publish(FormatError("bot output must be valid UTF-8"))
        except OSError as error:
            self._publish(error)
        finally:
            self._publish(None)

    def decode_reply(self, reply):
        if self._output_fault:
            raise FormatError(self._output_fault)
        return super().decode_reply(reply)

    def check_process(self):
        if self._output_fault:
            raise BotProcessError(self._output_fault)
        super().check_process()

    def _prepare_files(self):
        (self.box / "box" / "main.py").write_bytes(self.source)

    def _bot_arguments(self):
        return ["/usr/bin/python3", "-I", "-B", "-u", "/box/main.py"]
