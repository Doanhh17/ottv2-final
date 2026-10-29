"""Apply the selected host's slot plan, preserving old keys and retained results."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from ottv2 import worker_pool
from scripts.start_worker import HOSTS
from scripts.update_worker import atomic_write,stopped_host


def validate_replacement(old,new):
    if old['host_id']!=new['host_id'] or old['url']!=new['url'] or new['workers'][:len(old['workers'])]!=old['workers']:
        raise ValueError('Bộ khóa mới phải của đúng máy và giữ nguyên toàn bộ khóa cũ.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool-config',type=Path,help='extended JSON from the VPS, only needed for 12 slots')
    args=parser.parse_args()
    if not sys.platform.startswith('linux') or os.geteuid()!=0:
        parser.exit(1,'Chạy bằng sudo trong Ubuntu máy chấm.\n')
    import pwd
    credential=Path('/etc/ottv2-worker-pool.json')
    isolate=Path('/usr/local/etc/isolate')
    owner=pwd.getpwnam('ottv2')
    profile=Path(owner.pw_dir)/'.config/ottv2/worker-profile.json'
    service='ottv2-worker-pool.service'
    changed=False;backup={}
    try:
        old=worker_pool.load_config(credential)
        new=worker_pool.load_config(args.pool_config) if args.pool_config else old
        validate_replacement(old,new)
        selected=[(name,slots) for name,(host,slots) in HOSTS.items() if host==new['host_id']]
        if len(selected)!=1:raise ValueError('Không nhận diện được tên máy chấm.')
        name,slots=selected[0]
        if len(new['workers'])<slots:
            raise ValueError(f'{name} cần JSON đã mở rộng đủ {slots} khóa từ VPS; dùng --pool-config đường-dẫn-tệp.')
        # Validate resources before stopping a working service.
        worker_pool.resource_plan(slots)
        subprocess.run(['systemctl','stop',service],check=True)
        with stopped_host(),TemporaryDirectory(prefix='ottv2-worker-plan-') as tmp:
            for path in (credential,isolate,profile):
                if path.is_symlink():raise ValueError('Không dùng tệp cấu hình liên kết.')
                if path.exists():
                    info=path.stat();backup[path]=(path.read_bytes(),info.st_mode&0o777,(info.st_uid,info.st_gid))
                else:backup[path]=None
            target=Path(tmp)/'isolate'
            worker_pool.render_isolate_config(slots,target)
            changed=True
            if args.pool_config:atomic_write(credential,args.pool_config.read_bytes(),0o600,(owner.pw_uid,owner.pw_gid))
            atomic_write(isolate,target.read_bytes(),0o644,(0,0))
            subprocess.run([worker_pool.ISOLATE,'--check-config'],check=True,capture_output=True)
            subprocess.run(['sudo','-u','ottv2','python3','-m','ottv2.worker_pool','configure',
                '--config',str(credential),'--name',name,'--max-slots',str(slots)],cwd=ROOT,check=True)
        subprocess.run(['systemctl','start',service],check=True)
        print(f'DA CAU HINH: {name}, {slots} tran song song, phan hoi 2.5 giay/luot. Giu nguyen khoa cu va outbox.')
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as error:
        if changed:
            for path,saved in backup.items():
                if saved is None:path.unlink(missing_ok=True)
                else:atomic_write(path,*saved)
            subprocess.run(['systemctl','start',service],check=False)
        parser.exit(1,'CHUA DOI CAU HINH: '+str(error)+'\n')


if __name__=='__main__':main()
