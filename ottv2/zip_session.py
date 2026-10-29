"""Validate a local ZIP in memory; copy only main.py into Isolate."""

import hashlib
from io import BytesIO
from pathlib import Path
import stat
import struct
import zipfile
import zlib

from .file_session import IsolatedFileSession, MAX_SOURCE_BYTES

MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_UNPACKED_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 256


def check_directory(data):
    # Bound central-directory objects before ZipFile allocates ZipInfo objects.
    end = len(data)
    while True:
        end = data.rfind(b'PK\x05\x06', max(0, len(data) - 65557), end)
        if end < 0:
            raise ValueError("ZIP has no valid end record")
        if end + 22 <= len(data) and end + 22 + struct.unpack_from('<H', data, end + 20)[0] == len(data):
            break
    _, disk, cd_disk, disk_count, count, size, offset, _ = struct.unpack_from('<4s4H2LH', data, end)
    if disk or cd_disk or disk_count != count or count > MAX_ENTRIES or offset + size != end:
        raise ValueError("ZIP supports one disk, no ZIP64, at most 256 entries")
    pos, actual = offset, 0
    while pos < end:
        if pos + 46 > end or data[pos:pos + 4] != b'PK\x01\x02':
            raise ValueError("invalid ZIP central directory")
        lengths = struct.unpack_from('<3H', data, pos + 28)
        pos += 46 + sum(lengths)
        actual += 1
        if actual > MAX_ENTRIES:
            raise ValueError("ZIP exceeds 256 entries")
    if pos != end or actual != count:
        raise ValueError("ZIP directory entry count mismatch")


def read_submission_zip(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("bot ZIP must be a regular file, not a link")
    with path.open('rb') as stream:
        raw = stream.read(MAX_ZIP_BYTES + 1)
    if not raw or len(raw) > MAX_ZIP_BYTES:
        raise ValueError("ZIP must contain 1..5242880 bytes")
    check_directory(raw)
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries, names = [], set()
            declared = 0
            for item in archive.infolist():
                name = item.orig_filename
                parts = name.rstrip('/').split('/')
                if (not name or len(name) > 512 or any(not p or p in ('.', '..') for p in parts)
                        or any(c in name for c in ('\\', ':', '\x00'))
                        or any(ord(c) < 32 for c in name)):
                    raise ValueError("unsafe ZIP path")
                key = '/'.join(parts).casefold()
                if key in names:
                    raise ValueError("duplicate ZIP path ignoring case")
                names.add(key)
                mode = stat.S_IFMT(item.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or (mode == stat.S_IFDIR and not item.is_dir()):
                    raise ValueError("ZIP forbids links and special files")
                if item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError("ZIP forbids encryption or unsupported compression")
                declared += item.file_size
                if declared > MAX_UNPACKED_BYTES:
                    raise ValueError("ZIP unpacked size exceeds 20 MiB")
                entries.append((item, parts))
            files = [(item, parts) for item, parts in entries if not item.is_dir()]
            root_main = any(parts == ['main.py'] for _, parts in files)
            wrapped = (not root_main and bool(files) and all(len(p) >= 2 for _, p in files)
                       and len({p[0] for _, p in files}) == 1)
            source = None
            total = 0
            for item, parts in entries:
                normalized = parts[1:] if wrapped else parts
                selected = not item.is_dir() and normalized == ['main.py']
                if not item.is_dir() and parts[-1].lower().endswith(('.py', '.pyw', '.pyc', '.pyo')) and not selected:
                    raise ValueError("submission may contain only one Python file: main.py at root")
                data = bytearray() if selected else None
                entry_bytes = 0
                # Read every entry to validate CRC and bound actual decompressed bytes.
                with archive.open(item) as stream:
                    while chunk := stream.read(65536):
                        total += len(chunk)
                        entry_bytes += len(chunk)
                        if total > MAX_UNPACKED_BYTES:
                            raise ValueError("ZIP actual unpacked size exceeds 20 MiB")
                        if selected:
                            data.extend(chunk)
                            if len(data) > MAX_SOURCE_BYTES:
                                raise ValueError("main.py exceeds 5 MiB")
                if entry_bytes != item.file_size:
                    raise ValueError("ZIP entry size mismatch")
                if selected:
                    source = bytes(data)
            if not source:
                raise ValueError("ZIP needs nonempty main.py at root or under one wrapper folder")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, UnicodeError, zlib.error) as error:
        raise ValueError(f"invalid ZIP: {error}") from error
    return source, {"archive_sha256": hashlib.sha256(raw).hexdigest(),
                    "archive_bytes": len(raw), "unpacked_bytes": total}


class IsolatedZipSession(IsolatedFileSession):
    def __init__(self, path, box_id, seed=1):
        source, archive_info = read_submission_zip(path)
        self._initialize_source(source, box_id, seed)
        self.strategy = "zip"
        self.source_info.update(archive_info)
