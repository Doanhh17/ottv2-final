"""Update named project files only if the Linux snapshot is unchanged."""

import hashlib
from pathlib import Path
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    if not sys.platform.startswith("linux") or len(sys.argv) < 2:
        raise SystemExit("Run inside Ubuntu: python3 sync_linux_files.py relative-file.py ...")
    source = Path(__file__).resolve().parent.parent
    destination = Path.home() / "ottv2-local"
    manifest = destination / "SHA256SUMS"
    hashes = dict(reversed(line.split("  ", 1)) for line in manifest.read_text().splitlines())
    updates = []
    for name in dict.fromkeys(sys.argv[1:]):
        relative = Path(name)
        allowed_python = relative.suffix == ".py" and relative.parts[0] in ("ottv2", "tests", "scripts", "examples")
        allowed_js = relative.suffix == '.cjs' and relative.parts[0] in ('tests','scripts')
        allowed_page = relative.as_posix() in ("web/history.html", "web/portal.html", "replay/index.html")
        allowed_public = relative.as_posix() in {
            'README.md','.gitignore','config/google-resources.json','config/isolate-source.json',
            'google/Code.gs','google/appsscript.json','google/setup-password-form.gs',
            'deploy/nginx.conf.in','deploy/ottv2-proxy.conf.in','deploy/ottv2.service','deploy/ottv2-worker.service',
            'deploy/ottv2-worker-pool.service',
            'scripts/start-local.ps1','scripts/start-worker.cmd',
            'scripts/worker_sinh.cmd','scripts/worker_hai.cmd','scripts/worker_duong.cmd',
            'web/assets/uet-logo.png',*[f'docs/{name}.md' for name in
                ('README','MOI_TRUONG','CAI_DAT','VIET_BOT','THIET_KE','KIEM_THU','VAN_HANH',
                 'QUY_DINH_SINH_VIEN','TRIEN_KHAI_VPS','MAY_CHAM','BAN_DO')]}
        if relative.is_absolute() or ".." in relative.parts or not (allowed_python or allowed_js or allowed_page or allowed_public):
            raise SystemExit(f"STOP: unsupported path: {name}")
        src, target = source / relative, destination / relative
        if not src.resolve().is_relative_to(source) or not target.resolve().is_relative_to(destination):
            raise SystemExit(f"STOP: path leaves a project folder: {name}")
        key = relative.as_posix()
        if target.exists():
            if key not in hashes or digest(target.read_bytes()) != hashes[key]:
                raise SystemExit(f"STOP: local Linux file changed, no files updated: {key}")
        elif key in hashes:
            raise SystemExit(f"STOP: tracked Linux file is missing, no files updated: {key}")
        updates.append((key, target, src.read_bytes()))
    # Validate all requested paths before writing any file.
    for key, target, data in updates:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[key] = digest(data)
        print(f"SYNCED: {key}")
    manifest.write_text("".join(f"{value}  {key}\n" for key, value in sorted(hashes.items())))


if __name__ == "__main__":
    main()
