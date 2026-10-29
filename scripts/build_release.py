"""Bundle source only; private live data, credentials and backups are excluded."""

import argparse
import hashlib
from pathlib import Path
import zipfile


def source_files(root=None):
    """Shared allowlist for publishing sources and optional worker-update packages."""
    root=Path(root or Path(__file__).resolve().parent.parent).resolve()
    # Explicit source roots exclude runtime data, backups, VCS metadata and credentials.
    allowed={'ottv2':{'.py'},'tests':{'.py','.cjs'},'scripts':{'.py','.ps1','.cmd','.cjs'},
             'web':{'.html'},'replay':{'.html'},'practice':{'.html','.py','.js'},'examples':{'.py'},'maps':{'.json'},
             'deploy':{'.in','.service'},'google':{'.gs','.json'}}
    files=[root/'README.md',root/'web/assets/uet-logo.png',root/'config/google-resources.json',
           root/'config/isolate-source.json',root/'.gitignore',root/'ottv2/rules.py',root/'scripts/update_worker.py',
           root/'scripts/load_test_server.py',root/'scripts/measure_students.cjs',
           root/'scripts/start_worker.py',root/'scripts/install_worker_desktop.ps1',
           root/'deploy/ottv2-vps-worker.service',root/'practice/index.html',root/'practice/core.js',root/'practice/runtime.py']
    files.extend(root/'docs'/name for name in (
        'README.md','MOI_TRUONG.md','CAI_DAT.md','VIET_BOT.md','THIET_KE.md','KIEM_THU.md','VAN_HANH.md',
        'QUY_DINH_SINH_VIEN.md','TRIEN_KHAI_VPS.md','MAY_CHAM.md','BAN_DO.md','TOI_UU.md','DAU_THU.md'))
    files.extend(root/'google'/name for name in ('Code.gs','appsscript.json','setup-password-form.gs'))
    # Refuse an incomplete working copy instead of silently omitting a required component.
    for source in files:
        if not source.is_file():raise ValueError('Missing required release source: '+source.relative_to(root).as_posix())
    for folder,suffixes in allowed.items():
        files.extend(p for p in (root/folder).rglob('*') if p.suffix in suffixes and '__pycache__' not in p.parts)
    files.extend(p for p in (root/'docs').glob('*') if p.suffix in {'.md','.txt','.json'})
    sources=sorted(set(files))
    for source in sources:
        if source.is_symlink() or not source.resolve().is_relative_to(root):raise ValueError('linked release source')
    return sources


def build(destination):
    root=Path(__file__).resolve().parent.parent
    files=source_files(root)
    manifest={}
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as output,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for source in files:
            if source.is_symlink() or not source.resolve().is_relative_to(root): raise ValueError('linked release source')
            name=source.relative_to(root).as_posix();content=source.read_bytes()
            archive.writestr('ottv2/'+name,content)
            manifest[name]=hashlib.sha256(content).hexdigest()
        archive.writestr('ottv2/SHA256SUMS',''.join(digest+'  '+name+'\n' for name,digest in sorted(manifest.items())))
    return len(manifest)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try: print('Đã đóng gói '+str(build(args.output))+' tệp mã nguồn. Không kèm dữ liệu tài khoản hoặc khóa Google.')
    except (OSError,ValueError) as error: parser.exit(1,str(error)+'\n')
