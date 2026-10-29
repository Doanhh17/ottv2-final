"""Probe READY in Isolate before promoting a local bot version."""

import hashlib
import sys

from .file_session import IsolatedFileSession
from .isolated_session import BOX_ROOT, MEMORY_LIMIT_MIB
from .sample_session import BotProcessError, BotStartupError


class _ReadyAccepted(Exception):
    pass


class ReadySession(IsolatedFileSession):
    def __init__(self, source):
        self._initialize_source(source, 0, 1)
        # READY has its own 5s deadline. These are cleanup/backstop limits,
        # not the match CPU budget; no match is running during this probe.
        self.whole_wall_limit = 7
        self.whole_cpu_limit = 8


def check_ready(source):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('READY validation must run inside the configured Ubuntu Isolate environment')
    for box in (0, 1):
        if (BOX_ROOT / str(box)).exists() or (BOX_ROOT / str(box)).is_symlink():
            raise RuntimeError('a match or probe is using the sandbox; retry after it finishes')
    session = ReadySession(source)
    try:
        with session as bot:
            bot.check_process()
            bot._stop_process()
            # Deliberate termination is not a candidate runtime failure.
            # The exception path still collects metadata and cleans the box.
            raise _ReadyAccepted()
    except _ReadyAccepted:
        if session.memory_limit_detail() is not None:
            raise ValueError('READY validation failed: startup_memory_limit')
        meta = session.metadata
        stopped = meta.get('status') == 'SG' and meta.get('exitsig') == '15'
        exited = meta.get('status') is None and meta.get('exitcode') == '0'
        if not (stopped or exited):
            raise ValueError(f'READY validation failed while stopping candidate: {meta}')
    except BotStartupError as error:
        reason, detail = session.startup_error_detail(error)
        raise ValueError(f'READY validation failed: {reason} | {detail}') from error
    except BotProcessError as error:
        raise ValueError(f'READY validation failed: candidate stopped after READY | {error}') from error
    return {'ready': True, 'source_sha256': hashlib.sha256(source).hexdigest(),
            'startup_wall_seconds': session.startup_wall_seconds,
            'startup_limit_seconds': session.startup_timeout, 'memory_limit_mib': MEMORY_LIMIT_MIB}
