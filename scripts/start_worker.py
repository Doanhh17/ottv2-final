"""Start an installed worker using its existing key; never register a new key."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ottv2 import worker_pool

# Operator-selected limits for the four hosts.
HOSTS = {'worker_local': ('worker-local-pool', 6),
         'worker_sinh': ('worker-pc02', 12),
         'worker_hai': ('worker-pc03', 6),
         'worker_duong': ('worker-pc04', 12)}


def find_config(root, host_id):
    canonical = Path(root)/'output/private-workers/worker-local-pool-vps.json'
    if host_id == 'worker-local-pool' and canonical.is_file():
        if worker_pool.load_config(canonical)['host_id'] != host_id:
            raise ValueError('Khóa local không đúng máy. Xem docs/MAY_CHAM.md.')
        return canonical
    candidates = [Path('/etc/ottv2-worker-pool.json'),
                  *sorted((Path(root)/'output/private-workers').glob('*.json'))]
    matches = []
    for path in candidates:
        if path.is_file():
            try:
                if worker_pool.load_config(path)['host_id'] == host_id:
                    matches.append(path)
            except (ValueError, OSError):
                continue
    if len(matches) != 1:
        raise ValueError('Cần đúng một tệp khóa của ' + host_id + '. Xem docs/MAY_CHAM.md.')
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', choices=HOSTS)
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.exit(1, 'Mở bằng Ubuntu/WSL.\n')
    service = 'ottv2-worker-pool.service'
    installed = subprocess.run(['systemctl','cat',service],capture_output=True).returncode == 0
    try:
        if installed:
            if args.name:
                host, slots = HOSTS[args.name]
                # The service has its own HOME/profile and reads the same key.
                code = ('from pathlib import Path; from ottv2.worker_pool import load_config,load_profile,configure_profile; '
                        'p=Path("/etc/ottv2-worker-pool.json"); '
                        'assert load_config(p)["host_id"] == '+repr(host)+'; '
                        'old=load_profile('+repr(host)+'); '
                        'configure_profile(p,'+repr(args.name)+','+str(slots)+') if old is None else None; '
                        'print("configured" if old is None else "unchanged")')
                answer = subprocess.run(['sudo','-u','ottv2','/usr/bin/python3','-c',code],cwd=ROOT,check=True,capture_output=True,text=True)
                action = 'restart' if answer.stdout.strip() == 'configured' else 'start'
            else:
                action = 'start'
            subprocess.run(['sudo','systemctl',action,service],check=True)
            subprocess.run(['systemctl','--no-pager','--full','status',service],check=True)
            print('Đã bật dịch vụ. Giữ Windows thức; xem trạng thái máy chấm trên website.')
        else:
            running = subprocess.run(['pgrep','-u',str(os.getuid()),'-f',
                r'(^|/)python3( -u)? -m ottv2\.worker_pool run '],capture_output=True).returncode == 0
            if running:
                print('Máy chấm đang chạy. Không mở thêm tiến trình.'); return
            if args.name:
                host, slots = HOSTS[args.name]
                config = find_config(ROOT,host)
                if worker_pool.load_profile(host) is None:
                    worker_pool.configure_profile(config,args.name,slots)
            else:
                config = ROOT/'output/private-workers/worker-local-pool-vps.json'
                worker_pool.load_config(config)
            subprocess.run(['sudo','systemctl','start','isolate.service'],check=True)
            print('Giữ cửa sổ này mở. Dừng bằng Ctrl+C rồi chờ dọn xong.',flush=True)
            subprocess.run([sys.executable,'-u','-m','ottv2.worker_pool','run',
                '--config',str(config),'--directory',str(ROOT/'output/vps-worker-pool'),
                '--cpu-mode','shared'],cwd=ROOT,check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'Chưa bật được máy chấm: '+str(error)+'\n')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
