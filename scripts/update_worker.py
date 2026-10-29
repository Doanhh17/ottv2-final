"""Update an installed Linux worker from a source ZIP, preserving keys/outboxes."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import zipfile


def source_name(name):
    path=PurePosixPath(name)
    roots={'ottv2','tests','scripts','web','replay','practice','examples','maps','deploy','google','docs','config'}
    if ('\\' in name or path.is_absolute() or '..' in path.parts or not path.parts
            or name not in ('README.md','.gitignore') and path.parts[0] not in roots):
        raise ValueError('Đường dẫn không thuộc mã nguồn: '+name)
    return name


def read_archive(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if (len(names)!=len(set(names)) or len(names)>512
                or sum(i.file_size for i in archive.infolist())>32*1024*1024
                or any(i.file_size>8*1024*1024 or stat.S_ISLNK(i.external_attr>>16) for i in archive.infolist())):
            raise ValueError('Gói nguồn quá lớn, có mục trùng hoặc liên kết.')
        hashes=parse_manifest(archive.read('ottv2/SHA256SUMS').decode('utf-8'))
        if set(names)!={'ottv2/SHA256SUMS',*('ottv2/'+name for name in hashes)}:
            raise ValueError('Danh sách ZIP không khớp SHA256SUMS.')
        files={name:archive.read('ottv2/'+name) for name in hashes}
        if any(hashlib.sha256(files[name]).hexdigest()!=digest for name,digest in hashes.items()):
            raise ValueError('Mã băm ZIP không đúng.')
        if 'ottv2/rules.py' not in files or 'deploy/ottv2-worker-pool.service' not in files:
            raise ValueError('Gói thiếu thành phần cập nhật máy chấm.')
        files['SHA256SUMS']=archive.read('ottv2/SHA256SUMS')
        return files


def parse_manifest(text):
    result={}
    for line in text.splitlines():
        digest,name=line.split('  ',1)
        source_name(name)
        if name in result or not re.fullmatch('[a-f0-9]{64}',digest):
            raise ValueError('SHA256SUMS chưa đúng.')
        result[name]=digest
    if not result:raise ValueError('Danh sách nguồn rỗng.')
    return result


def check_installed(project):
    project=Path(project).resolve()
    hashes=parse_manifest((project/'SHA256SUMS').read_text(encoding='utf-8'))
    for name,digest in hashes.items():
        path=project/name
        if (path.is_symlink() or not path.resolve().is_relative_to(project)
                or hashlib.sha256(path.read_bytes()).hexdigest()!=digest):
            raise ValueError('Tệp đang dùng đã sửa riêng, chưa ghi đè: '+name)
    return hashes


def atomic_write(path,data,mode=0o644,owner=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.ottv2-update')
    created=False
    try:
        with temporary.open('xb') as stream:
            created=True
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        temporary.chmod(mode)
        if owner is not None and hasattr(os,'chown'):os.chown(temporary,*owner)
        os.replace(temporary,path)
    finally:
        if created:temporary.unlink(missing_ok=True)


def install_source(archive,project,backup):
    project=Path(project).resolve();backup=Path(backup).resolve()
    if backup.is_relative_to(project):raise ValueError('Bản khôi phục phải nằm ngoài thư mục mã.')
    files=read_archive(archive)
    old=check_installed(project)
    changed={name for name,data in files.items() if not (project/name).exists() or (project/name).read_bytes()!=data}
    removed=set(old)-set(files)
    if not changed and not removed:return {'changed':0,'removed':0}
    metadata={}
    for name in changed|removed:
        path=project/name
        if path.is_symlink() or not path.resolve().is_relative_to(project):raise ValueError('Đích không hợp lệ.')
        if path.exists():
            if name not in old and name!='SHA256SUMS':raise ValueError('Tệp riêng chưa có trong bản cũ: '+name)
            info=path.stat();metadata[name]=(stat.S_IMODE(info.st_mode),(info.st_uid,info.st_gid))
    backup.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name in metadata:
        path=backup/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        path.write_bytes((project/name).read_bytes());path.chmod(0o600)
    written=[]
    try:
        for name in sorted(changed,key=lambda value:(value=='SHA256SUMS',value)):
            mode,owner=metadata.get(name,(0o644,None))
            atomic_write(project/name,files[name],mode,owner);written.append(name)
        for name in removed:(project/name).unlink();written.append(name)
        check_installed(project)
    except BaseException:
        for name in reversed(written):
            if name in metadata:
                mode,owner=metadata[name];atomic_write(project/name,(backup/name).read_bytes(),mode,owner)
            else:(project/name).unlink(missing_ok=True)
        raise
    return {'changed':len(changed),'removed':len(removed)}


@contextmanager
def stopped_host():
    import fcntl
    import pwd
    path=Path('/tmp/ottv2-worker-pool.lock')
    try:fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
    except FileNotFoundError:
        fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        owner=pwd.getpwnam('ottv2');os.fchown(fd,owner.pw_uid,owner.pw_gid)
    with os.fdopen(fd,'r+b') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Khóa máy chấm không hợp lệ.')
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Máy còn bản chấm chạy tay. Dừng bằng Ctrl+C rồi chạy lại.') from None
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--pool-config',type=Path,help='extended worker JSON; original slot keys must match')
    args=parser.parse_args()
    if not sys.platform.startswith('linux') or os.geteuid()!=0:
        parser.exit(1,'Chạy trong Ubuntu bằng sudo python3.\n')
    project=Path('/opt/ottv2/app')
    service='ottv2-worker-pool.service'
    installed=subprocess.run(['systemctl','cat',service],capture_output=True).returncode==0
    started=False
    try:
        read_archive(args.archive);check_installed(project)
        if installed:
            print('Dang dung dich vu; cho tran dang cham ket thuc...',flush=True)
            # Give an old unit the new graceful deadline before stopping it.
            dropin=Path('/run/systemd/system')/(service+'.d')/'90-ottv2-update-timeout.conf'
            if dropin.exists():raise ValueError('Còn cấu hình cập nhật tạm; kiểm lần trước trước khi chạy lại.')
            atomic_write(dropin,b'[Service]\nTimeoutStopSec=330\n')
            subprocess.run(['systemctl','daemon-reload'],check=True)
            subprocess.run(['systemctl','stop',service],check=True)
        backup=Path('/opt/ottv2')/('before-worker-update-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
        with stopped_host():
            answer=install_source(args.archive,project,backup)
            if installed:
                subprocess.run(['install','-m','644',str(project/'deploy'/service),'/etc/systemd/system/'+service],check=True)
        if installed:
            dropin.unlink()
            subprocess.run(['systemctl','daemon-reload'],check=True)
            configure=['python3',str(project/'scripts/configure_worker.py')]
            if args.pool_config:configure+=['--pool-config',str(args.pool_config.absolute())]
            subprocess.run(configure,check=True);started=True
        print('DA CAP NHAT: 7 giay CPU / 2.5 giay phan hoi / 25 nuoc moi ben / so quan khi het nuoc; giu nguyen khoa va outbox.',flush=True)
        print('Tep nguon da doi:',answer['changed'])
        if started:subprocess.run(['systemctl','--no-pager','--full','status',service],check=True)
        else:print('Chua co dich vu: tiep tuc muc cai dich vu trong docs/MAY_CHAM.md.')
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError,zipfile.BadZipFile) as error:
        parser.exit(1,'CHUA CAP NHAT XONG: '+str(error)+'\nKiem tra loi; khong xoa khoa/outbox.\n')


if __name__=='__main__':main()
