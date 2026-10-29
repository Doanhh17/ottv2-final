"""Keep one trusted sample bot alive across turns. Not an isolated Worker."""

from collections import deque
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
from time import monotonic

from .engine import FormatError
from .protocol import state_message, validate_ready

ROOT = Path(__file__).resolve().parent.parent


class BotProcessError(RuntimeError):
    """A bot stopped or closed a required channel; not a host I/O error."""


class BotReplyTimeout(RuntimeError):
    """No complete reply arrived before the requested deadline."""


class BotStartupError(RuntimeError):
    def __init__(self, reason, detail):
        self.reason, self.detail = reason, detail
        super().__init__(detail)


class SampleSession:
    def __init__(self, strategy, seed=1, response_timeout=5, startup_timeout=5):
        if strategy not in ("random", "greedy", "search", "retry-demo", "alpha", "guardian"):
            raise ValueError("only the bundled sample strategies are supported")
        self.strategy = strategy
        self.seed = seed
        self.response_timeout = response_timeout
        self.startup_timeout = startup_timeout
        self.startup_wall_seconds = None
        self.ready = False
        self.process = None
        self._replies = Queue()
        self._errors = deque(maxlen=8)
        self._threads = []

    def __enter__(self):
        started = monotonic()
        self.process = self._start_process()
        self._threads = [Thread(target=self._read_replies, daemon=True),
                         Thread(target=self._read_errors, daemon=True)]
        for thread in self._threads:
            thread.start()
        try:
            remaining = max(0, self.startup_timeout - (monotonic() - started))
            try:
                message = self._receive_json(remaining, "send READY")
            except BotReplyTimeout as error:
                raise BotStartupError("startup_timeout", str(error)) from error
            except FormatError as error:
                raise BotStartupError("startup_format_error", str(error)) from error
            except BotProcessError as error:
                raise BotStartupError("startup_runtime_error", str(error)) from error
            if monotonic() - started > self.startup_timeout:
                raise BotStartupError("startup_timeout",
                                      f"{self.strategy} bot exceeded {self.startup_timeout:g}s startup wall limit")
            try:
                validate_ready(message)
            except ValueError as error:
                raise BotStartupError("startup_format_error", str(error)) from error
            self.startup_wall_seconds = monotonic() - started
            self.ready = True
        except BaseException as error:
            # __exit__ is not called by Python when __enter__ raises.
            try:
                if isinstance(error, BotStartupError) and self.process.poll() is None:
                    if error.reason == "startup_runtime_error":
                        try:
                            self.process.wait(timeout=0.1)
                        except subprocess.TimeoutExpired:
                            pass
                    if self.process.poll() is None:
                        self._stop_process()
            finally:
                self.close()
            raise
        return self

    def _start_process(self):
        return subprocess.Popen(
            [sys.executable, "-m", "ottv2.bot_process", "--strategy", self.strategy,
             "--seed", str(self.seed)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, cwd=ROOT,
        )

    def _stop_process(self):
        self.process.kill()

    def _read_replies(self):
        try:
            while True:
                line = self.process.stdout.readline(65537)
                if not line:
                    return
                if len(line) > 65536 or not line.endswith("\n"):
                    self._replies.put(FormatError("bot response needs one complete JSON line, max 65536 characters"))
                    return
                self._replies.put(line)
        except UnicodeError:
            self._replies.put(FormatError("bot output must be valid UTF-8"))
        except OSError as error:
            self._replies.put(error)
        finally:
            self._replies.put(None)

    def _read_errors(self):
        # Drain stderr too: a full error pipe must not block the bot's stdout.
        try:
            while chunk := self.process.stderr.read(1024):
                self._errors.append(chunk)
        except (OSError, UnicodeError):
            pass

    def request_move(self, game):
        self.send_state(game)
        return self._receive_json(self.response_timeout, "reply to state")

    def send_state(self, game, time_info=None):
        if not self.ready:
            raise RuntimeError(f"{self.strategy} bot has not reported READY")
        self.check_process()
        try:
            self.process.stdin.write(json.dumps(state_message(game, time_info)) + "\n")
            self.process.stdin.flush()
        except BrokenPipeError as error:
            raise BotProcessError(f"{self.strategy} bot closed its input") from error
        except OSError as error:
            raise RuntimeError(f"{self.strategy} bot closed its input") from error

    def check_process(self):
        if self.process is None:
            raise RuntimeError("bot process was not created")
        if self.process.poll() is not None:
            raise BotProcessError(f"{self.strategy} bot process stopped")

    def _receive_json(self, timeout, action):
        try:
            reply = self._replies.get(timeout=timeout)
        except Empty as error:
            raise BotReplyTimeout(
                f"{self.strategy} bot did not {action} before the wall-time deadline"
            ) from error
        return self.decode_reply(reply)

    def decode_reply(self, reply):
        if reply is None:
            raise BotProcessError(f"{self.strategy} bot closed its output")
        if isinstance(reply, FormatError):
            raise reply
        if isinstance(reply, Exception):
            raise RuntimeError(f"{self.strategy} bot output read error: {reply}") from reply
        try:
            return json.loads(reply)
        except (ValueError, RecursionError) as error:
            # Includes malformed JSON, excessive integer length and nesting.
            # Keep I/O and engine/configuration failures out of this category.
            raise FormatError(f"bot output is not valid supported JSON: {error}") from error

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        self.ready = False
        if self.process is None:
            return
        try:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._stop_process()
                self.process.wait(timeout=2)
        finally:
            for thread in self._threads:
                thread.join(timeout=2)
            self.process.stdout.close()
            self.process.stderr.close()
