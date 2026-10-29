"""Private, verified snapshots for a stopped local arena; restore to a new folder."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
from tempfile import TemporaryDirectory

from . import accounts, elo_store, match_queue

MAX_BYTES = 4 * 1024**3


def _safe(path):
    path = Path(path).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('backup paths must not contain links')
    return path


def _hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()


def _relative(path,root):
    try: return _safe(path).relative_to(root)
    except ValueError: raise ValueError('all live data must be inside the chosen data directory') from None


def create(directory, destination):
    """Caller must stop the arena and hold the global Worker lock."""
    root,destination=_safe(directory),_safe(destination)
    if not root.is_dir() or destination.exists() or destination.is_relative_to(root):
        raise ValueError('use an existing data directory and a new backup path outside it')
    queue=root/'queue.sqlite3'
    with closing(elo_store._connect(queue,readonly=True)) as connection:
        match_queue._schema(connection)
        cfg=match_queue._config(connection)
    databases={Path('queue.sqlite3')}
    if (root/'accounts.sqlite3').exists(): databases.add(Path('accounts.sqlite3'))
    if cfg['ratings'] and Path(cfg['ratings']).exists(): databases.add(_relative(cfg['ratings'],root))
    store=_relative(cfg['store'],root)
    files=[]
    for folder in (root/store, root/'matches'):
        if not folder.exists(): continue
        _safe(folder)
        for path in folder.rglob('*'):
            _safe(path)
            if path.is_file(): files.append(path.relative_to(root))
    if (root/'google-client.json').exists(): files.append(Path('google-client.json'))
    if len(files)+len(databases)>100000:
        raise ValueError('too many backup files')
    total=sum((root/p).stat().st_size for p in [*files,*databases])
    if total>MAX_BYTES: raise ValueError('backup exceeds 4 GiB limit')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='ottv2-backup-') as temporary:
        stage=Path(temporary)
        for relative in databases:
            source=_safe(root/relative)
            target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True)
            with closing(elo_store._connect(source,readonly=True)) as src,closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
        for relative in files:
            target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(_safe(root/relative),target)
        manifest={'version':1,'source_root':str(root),'files':{p.relative_to(stage).as_posix():_hash(p)
                    for p in sorted(stage.rglob('*')) if p.is_file()}}
        (stage/'backup-manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        # Exclusive creation: an existing backup is never replaced.
        with destination.open('xb') as stream:
            os.chmod(destination,0o600)
            with tarfile.open(fileobj=stream,mode='w:gz') as archive:
                for path in sorted(stage.rglob('*')):
                    if path.is_file(): archive.add(path,arcname=path.relative_to(stage).as_posix(),recursive=False)
    return {'state':'backup_created','files':len(manifest['files']),'bytes':destination.stat().st_size}


def restore(archive_path,directory):
    root,archive_path=_safe(directory),_safe(archive_path)
    if root.exists() or not root.parent.is_dir(): raise ValueError('restore destination must be a new directory')
    with TemporaryDirectory(prefix='ottv2-restore-',dir=root.parent) as temporary:
        stage=Path(temporary)
        with tarfile.open(archive_path,'r:gz') as archive:
            members=archive.getmembers()
            if len(members)>100001 or sum(m.size for m in members)>MAX_BYTES: raise ValueError('backup is too large')
            names=set()
            for member in members:
                name=PurePosixPath(member.name)
                if (not member.isfile() or name.is_absolute() or '..' in name.parts or '\\' in member.name
                        or ':' in member.name or member.name in names): raise ValueError('unsafe backup entry')
                names.add(member.name)
                target=stage/Path(*name.parts);target.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as src,target.open('xb') as dst: shutil.copyfileobj(src,dst)
                target.chmod(0o600)
        manifest=json.loads((stage/'backup-manifest.json').read_text(encoding='utf-8'))
        if manifest.get('version')!=1 or set(manifest['files'])!=names-{'backup-manifest.json'}:
            raise ValueError('invalid backup manifest')
        for name,digest in manifest['files'].items():
            if _hash(stage/name)!=digest: raise ValueError('backup checksum mismatch')
        old=Path(manifest['source_root'])
        def relocate(value):
            return str(root/Path(value).relative_to(old)) if value else value
        queue=stage/'queue.sqlite3'
        with match_queue._write(queue) as connection:
            cfg=match_queue._config(connection)
            cfg['store'],cfg['ratings']=relocate(cfg['store']),relocate(cfg['ratings'])
            connection.execute('UPDATE config SET data=? WHERE id=1',(json.dumps(cfg),))
            for row in connection.execute('SELECT match_id,payload,result_path FROM jobs').fetchall():
                job=json.loads(row['payload']);job['bot_store']=relocate(job['bot_store'])
                connection.execute('UPDATE jobs SET payload=?,result_path=? WHERE match_id=?',
                                   (json.dumps(job),relocate(row['result_path']),row['match_id']))
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='cluster_leases'").fetchone():
                connection.execute("UPDATE cluster_leases SET expires_at=0 WHERE state='active'")
                connection.execute('UPDATE cluster_workers SET last_seen=0')
        account_db=stage/'accounts.sqlite3'
        if account_db.exists():
            with accounts.database(account_db,write=True) as connection:
                connection.execute('UPDATE settings SET queue=?',(str(root/'queue.sqlite3'),))
                connection.execute('DELETE FROM sessions')
        (stage/'backup-manifest.json').unlink()
        stage.chmod(0o700)
        # Move the completed staging tree only after all hashes/schema checks pass.
        stage.rename(root)
    return {'state':'backup_restored','files':len(manifest['files']),'sessions_revoked':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    save=sub.add_parser('create');save.add_argument('directory',type=Path);save.add_argument('archive',type=Path)
    load=sub.add_parser('restore');load.add_argument('archive',type=Path);load.add_argument('directory',type=Path)
    args=parser.parse_args()
    try:
        from .local_worker import _worker_lock
        from .cluster import master_lock
        with master_lock(), _worker_lock():
            answer=create(args.directory,args.archive) if args.command=='create' else restore(args.archive,args.directory)
        print(json.dumps(answer))
    except (OSError,ValueError,RuntimeError,KeyError,sqlite3.Error,tarfile.TarError):
        parser.exit(1,'Chưa sao lưu/khôi phục được. Dừng máy chấm; kiểm tra đường dẫn và dùng tên đích mới.\n')


if __name__=='__main__': main()
