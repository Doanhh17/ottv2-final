"""Prepare explicit local demo accounts and sample bots without resetting existing data."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from . import accounts, bot_store, match_queue, portal_data

DEMO_PASSWORD = 'OTTv2-demo-2026'


def prepare(directory):
    root = Path(directory).absolute()
    root.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent.parent
    queue = root / 'queue.sqlite3'
    if queue.exists():
        store = Path(portal_data.config(queue)['store'])
    else:
        store = root / 'bots'
        store.mkdir(exist_ok=True)
        match_queue.initialize(queue, store, project / 'maps/nine-piece.json', root / 'elo.sqlite3')
    with TemporaryDirectory(prefix='ottv2-demo-') as tmp:
        zipped = Path(tmp) / 'sample.zip'
        with zipfile.ZipFile(zipped, 'w') as archive:
            archive.write(project / 'examples/greedy/main.py', 'main.py')
        for identity in ('student01', 'student02', 'student03'):
            if (store / identity / 'current.json').exists():
                bot_store.load_bot(store, identity)
            else:
                bot_store.import_zip(store, identity, zipped)
    account_db = root / 'accounts.sqlite3'
    accounts.initialize(account_db, queue)
    for identity in ('admin', 'student01', 'student02', 'student03'):
        with accounts.database(account_db) as connection:
            exists = connection.execute('SELECT 1 FROM users WHERE identity=?', (identity,)).fetchone()
        if not exists:
            accounts.create_user(account_db, identity, DEMO_PASSWORD,
                                 'Quản trị viên' if identity == 'admin' else identity,
                                 'admin' if identity == 'admin' else 'student')
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('output/local-arena'))
    parser.add_argument('--demo', action='store_true', required=True,
                        help='explicitly create local demo accounts with a published demo password')
    args = parser.parse_args()
    try:
        root = prepare(args.directory)
        print(f'Đã chuẩn bị bản thử tại {root}. Dữ liệu có sẵn được giữ nguyên.')
        print('Tài khoản mẫu: admin, student01, student02, student03.')
        print('Mật khẩu mẫu cho tài khoản mới: ' + DEMO_PASSWORD)
        print('Chỉ dùng các tài khoản mẫu để thử trên máy cá nhân.')
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f'Chưa chuẩn bị được: {error}\n')


if __name__ == '__main__':
    main()
