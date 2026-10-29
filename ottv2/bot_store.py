"""Local content-addressed main.py versions with an atomic current pointer."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from .file_session import IsolatedFileSession, MAX_SOURCE_BYTES
from .zip_session import read_submission_zip
from .ready_check import check_ready


def validate_bot_id(bot_id):
    reserved = {'con', 'prn', 'aux', 'nul'} | {f'{p}{n}' for p in ('com', 'lpt') for n in range(1, 10)}
    if not isinstance(bot_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', bot_id) or bot_id in reserved:
        raise ValueError('bot id needs 1..40 lowercase letters/digits/_/-, not a reserved name')


def bot_directory(root, bot_id, create=False):
    validate_bot_id(bot_id)
    root = Path(root)
    directory = root / bot_id
    for path in (root, directory, directory / 'versions'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('bot store forbids links/junctions')
        if create:
            path.mkdir(parents=True, exist_ok=True)
        elif not path.is_dir():
            raise ValueError('bot store or bot id does not exist')
    return directory


def read_regular(path, limit):
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError('bot store artifact must be a regular file')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('bot store artifact exceeds its size limit')
    return data


def publish_source(path, source):
    temporary = None
    try:
        with NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.source-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_regular(path, MAX_SOURCE_BYTES) != source:
                raise ValueError('stored version is corrupt; refusing to overwrite')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def replace_current(path, record):
    if path.is_symlink() or path.is_junction():
        raise ValueError('current pointer must not be a link')
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=path.parent,
                                prefix='.current-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, sort_keys=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_zip(root, bot_id, zip_path, *, origin=None):
    # Validate the entire ZIP before changing the store. Code runs only in Isolate.
    source, archive = read_submission_zip(zip_path)
    version = hashlib.sha256(source).hexdigest()
    directory = bot_directory(root, bot_id, create=True)
    lock = directory / '.update.lock'
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise RuntimeError('bot update already locked; inspect .update.lock before retrying') from error
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(str(os.getpid()))
        validation = check_ready(source)
        publish_source(directory / 'versions' / f'{version}.py', source)
        record = {'schema_version': 1, 'bot_id': bot_id, 'version': version,
                  'entrypoint': 'main.py', 'validation': validation, **archive}
        if origin is not None:
            record['origin'] = origin
        replace_current(directory / 'current.json', record)
        return record
    finally:
        lock.unlink()


def load_version(root, bot_id, version):
    directory = bot_directory(root, bot_id)
    if not isinstance(version, str) or not re.fullmatch(r'[0-9a-f]{64}', version):
        raise ValueError('invalid stored bot version')
    source = read_regular(directory / 'versions' / (version + '.py'), MAX_SOURCE_BYTES)
    if not source or hashlib.sha256(source).hexdigest() != version:
        raise ValueError('bot source hash mismatch')
    return source


def load_metadata(root, bot_id):
    directory = bot_directory(root, bot_id)
    try:
        record = json.loads(read_regular(directory / 'current.json', 16384).decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid current bot pointer') from error
    if (not isinstance(record, dict) or type(record.get('schema_version')) is not int
            or record['schema_version'] != 1 or record.get('bot_id') != bot_id
            or record.get('entrypoint') != 'main.py'
            or not isinstance(record.get('version'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', record['version'])):
        raise ValueError('invalid current bot metadata')
    validation = record.get('validation')
    if (not isinstance(validation, dict) or validation.get('ready') is not True
            or validation.get('source_sha256') != record['version']):
        raise ValueError('bot needs READY validation; import its ZIP again inside Ubuntu')
    return record


def load_bot(root, bot_id):
    record = load_metadata(root, bot_id)
    source = load_version(root, bot_id, record['version'])
    return source, record


class IsolatedStoredSession(IsolatedFileSession):
    def __init__(self, root, bot_id, box_id, seed=1):
        source, metadata = load_bot(root, bot_id)
        self._initialize_source(source, box_id, seed)
        self.strategy = 'stored'
        self.source_info.update(bot_id=bot_id, version=metadata['version'])
        if 'origin' in metadata:
            self.source_info['origin'] = metadata['origin']


class IsolatedPinnedSession(IsolatedFileSession):
    """Run an immutable version already accepted into the trusted local store."""

    def __init__(self, root, bot_id, version, box_id, seed=1, origin=None):
        self._initialize_source(load_version(root, bot_id, version), box_id, seed)
        self.strategy = 'stored'
        self.source_info.update(bot_id=bot_id, version=version)
        if origin is not None:
            self.source_info['origin'] = origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    importing = commands.add_parser('import')
    importing.add_argument('bot_id')
    importing.add_argument('zip_path', type=Path)
    showing = commands.add_parser('show')
    showing.add_argument('bot_id')
    args = parser.parse_args()
    try:
        if args.command == 'import':
            record = import_zip(args.root, args.bot_id, args.zip_path)
            print(f"BOT SAVED: {record['bot_id']} | version={record['version']}")
        else:
            _, record = load_bot(args.root, args.bot_id)
            print(f"BOT CURRENT: {record['bot_id']} | version={record['version']}")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f'BOT STORE ERROR: {error}\n')


if __name__ == '__main__':
    main()
