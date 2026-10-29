"""Write a complete local result without replacing an existing match file."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def check_result_path(path):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"result file already exists; refusing to overwrite: {path}")
    return path


def write_result(path, record):
    path = check_result_path(path)
    data = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                prefix=".match-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Publish the completed file atomically. link() also refuses a target
        # created by another run since the initial existence check.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
