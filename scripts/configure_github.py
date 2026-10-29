"""Enter a GitHub public-repository token privately on the VPS."""
import getpass
import os
from pathlib import Path
import pwd
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from update_worker import atomic_write


def main():
    if not sys.platform.startswith('linux') or os.geteuid() != 0:
        raise SystemExit('Chạy trên VPS: sudo python3 scripts/configure_github.py')
    env_path = Path('/etc/ottv2.env')
    token_path = Path('/etc/ottv2-github.token')
    if env_path.is_symlink() or token_path.is_symlink():
        raise SystemExit('Không dùng đường dẫn liên kết.')
    text = env_path.read_text(encoding='utf-8')
    token = getpass.getpass('GitHub token chỉ đọc kho công khai (không hiện khi nhập): ').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{20,512}',token):
        raise SystemExit('Token chưa đúng định dạng; chưa đổi cấu hình.')
    owner = pwd.getpwnam('ottv2')
    atomic_write(token_path,(token+'\n').encode('ascii'),0o600,(owner.pw_uid,owner.pw_gid))
    lines = [line for line in text.splitlines() if not line.startswith('OTTV2_GITHUB_TOKEN_FILE=')]
    lines.append('OTTV2_GITHUB_TOKEN_FILE=/etc/ottv2-github.token')
    atomic_write(env_path,('\n'.join(lines)+'\n').encode('utf-8'),0o600,(0,0))
    print('Đã lưu riêng token. Chạy: sudo systemctl restart ottv2.service')


if __name__ == '__main__':
    main()
