"""Generate reviewable server configuration; never install or start services."""

import argparse
from pathlib import Path
import re


def render(domain, destination, *, distributed=False):
    domain=domain.lower()
    labels=domain.split('.')
    if (len(domain)>253 or len(labels)<2 or labels[-1].isdigit()
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
        raise ValueError('Dùng tên miền thật, không kèm https://, cổng hoặc đường dẫn.')
    root=Path(__file__).resolve().parent.parent
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    for source,target in (('nginx.conf.in','ottv2.conf'),('ottv2-proxy.conf.in','ottv2-proxy.conf')):
        (destination/target).write_text((root/'deploy'/source).read_text(encoding='utf-8').replace('@@DOMAIN@@',domain),encoding='utf-8')
    service=(root/'deploy/ottv2.service').read_text(encoding='utf-8')
    if distributed:service=service.replace('--port 8765','--port 8765 --distributed')
    (destination/'ottv2.service').write_text(service,encoding='utf-8')
    (destination/'ottv2-worker.service').write_bytes((root/'deploy/ottv2-worker.service').read_bytes())
    (destination/'ottv2-worker-pool.service').write_bytes((root/'deploy/ottv2-worker-pool.service').read_bytes())
    (destination/'ottv2.env').write_text('OTTV2_PUBLIC_ORIGIN=https://'+domain+'\n',encoding='utf-8')
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--distributed',action='store_true')
    args=parser.parse_args()
    try: print('Đã tạo cấu hình: '+str(render(args.domain,args.output,distributed=args.distributed)))
    except (ValueError,OSError) as error: parser.exit(1,str(error)+'\n')
