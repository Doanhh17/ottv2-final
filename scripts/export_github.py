"""Export only current public sources; never copy Git history or runtime data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts.build_release import source_files

SECRET_PATTERNS=(
    re.compile(rb'^-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----',re.M),
    re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,255}\b'),
    re.compile(rb'\bgithub_pat_[A-Za-z0-9_]{50,255}\b'),
    re.compile(rb'"type"\s*:\s*"service_account"'),
)

def public_rows():
    rows=[]
    for path in source_files(ROOT):
        data=path.read_bytes();name=path.relative_to(ROOT).as_posix()
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):raise ValueError('Possible credential in source: '+name)
        rows.append((name,data))
    return rows

def manifest(rows):
    return ''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in sorted(rows))

def export(destination):
    destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise ValueError('Choose a new folder; existing work is never overwritten.')
    rows=public_rows()
    destination.mkdir(parents=True,exist_ok=False)
    hashes={}
    for name,data in rows:
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        hashes[name]=hashlib.sha256(data).hexdigest()
    (destination/'SHA256SUMS').write_text(manifest(rows),encoding='utf-8',newline='\n')
    assert not(destination/'.git').exists() and not(destination/'output').exists()
    return {'state':'github_source_ready','source_files':len(rows),'folder':str(destination),'git_history_copied':False,'runtime_data_copied':False,'credential_pattern_scan':'passed'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--output',type=Path);mode.add_argument('--refresh-manifest',action='store_true')
    args=parser.parse_args()
    try:
        if args.refresh_manifest:
            rows=public_rows();(ROOT/'SHA256SUMS').write_text(manifest(rows),encoding='utf-8',newline='\n');print(json.dumps({'state':'manifest_updated','source_files':len(rows)}))
        else:print(json.dumps(export(args.output),ensure_ascii=False))
    except (OSError,ValueError) as error:parser.exit(1,str(error)+'\n')
