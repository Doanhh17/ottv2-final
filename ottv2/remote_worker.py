"""Pull pinned matches from an HTTPS master and run them in local Isolate boxes."""

import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Event, Thread
import time
import urllib.error
import urllib.request

from . import bot_store, cluster, elo_store, local_worker
from .file_session import MAX_SOURCE_BYTES
from .isolated_session import BOX_ROOT, ISOLATE, checked, validate_box_pair
from .match_archive import MAX_LOG_BYTES, read_match
from .map_config import reject_duplicates
from .rules import RULES_HEADER, RULES_VERSION


class RPCError(RuntimeError):
    def __init__(self,status):
        self.status=status
        super().__init__(f'worker RPC failed ({status})')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RPCError(502)


def load_config(path):
    path=elo_store._database_path(path,'Worker configuration')
    if path.stat().st_size>8192 or os.name!='nt' and path.stat().st_mode & 0o077:
        raise ValueError('worker configuration must be private (mode 600)')
    cfg=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=reject_duplicates)
    if set(cfg)!={'url','worker_id','token'} or not re.fullmatch('[a-f0-9]{64}',cfg['token']):
        raise ValueError('invalid worker configuration')
    bot_store.validate_bot_id(cfg['worker_id'])
    cfg['url']=cluster.master_url(cfg['url'])
    return cfg


class Client:
    def __init__(self,cfg): self.cfg=cfg

    def call(self,request):
        raw=json.dumps(request,allow_nan=False,separators=(',',':')).encode('utf-8')
        if len(raw)>cluster.MAX_RPC_BYTES: raise ValueError('worker request too large')
        req=urllib.request.Request(self.cfg['url']+'/api/worker',data=raw,method='POST',
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.cfg['token'],
                     RULES_HEADER:str(RULES_VERSION)})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as response:
                body=response.read(cluster.MAX_RPC_BYTES+1)
        except urllib.error.HTTPError as error:
            status=error.code;error.close()
            raise RPCError(status) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            # Socket timeouts and interrupted response bodies need the same
            # retry/outbox path as a failed connection, including embedded workers.
            raise RPCError(503) from None
        if len(body)>cluster.MAX_RPC_BYTES: raise ValueError('worker response too large')
        answer=json.loads(body.decode('utf-8'),object_pairs_hook=reject_duplicates,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite response')))
        if not isinstance(answer,dict): raise ValueError('invalid worker response')
        return answer


def _save(path,record):
    path=elo_store._database_path(path,'Worker outbox')
    temporary=None
    try:
        with NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);temporary.chmod(0o600)
            json.dump(record,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def flush(client,path):
    if not path.exists(): return None
    if path.is_symlink() or path.stat().st_size>cluster.MAX_RPC_BYTES:
        raise ValueError('invalid worker outbox')
    payload=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=reject_duplicates)
    try: answer=client.call(payload)
    except RPCError as error:
        if error.status!=409: raise
        answer={'state':'lease_rejected','match_id':payload['match_id']}
    # Only acknowledged or definitively fenced results can leave this outbox.
    if answer.get('state') not in ('completed','retry_pending','failed','lease_rejected'):
        raise ValueError('unexpected result acknowledgment')
    path.unlink()
    return answer


def execute(client,assignment,directory,*,runner=None,box_ids=(0,1)):
    box_ids=validate_box_pair(box_ids)
    job=assignment['job']
    bot_store.validate_bot_id(client.cfg['worker_id'])
    if set(assignment['sources'])!={'A','B'}: raise ValueError('missing bot sources')
    local_worker._validate_job(job)
    lease={'match_id':job['match_id'],'lease_token':assignment['lease_token']}
    if not re.fullmatch('[a-f0-9]{64}',lease['lease_token']): raise ValueError('invalid lease token')
    stopped=Event();lost=Event()
    def heartbeat():
        while not stopped.wait(10):
            try: client.call({'action':'heartbeat',**lease})
            except RPCError as error:
                if error.status in (401,403,409): lost.set();return
            except (OSError,ValueError): pass
    beat=Thread(target=heartbeat,daemon=True)
    beat.start()
    try:
        with TemporaryDirectory(prefix='job-',dir=directory) as temp:
            root=Path(temp);store=root/'bots';store.mkdir(mode=0o700)
            for side in ('A','B'):
                encoded=assignment['sources'][side]
                if not isinstance(encoded,str) or len(encoded)>((MAX_SOURCE_BYTES+2)//3)*4:
                    raise ValueError('bot source exceeds limit')
                source=base64.b64decode(encoded,validate=True)
                player=job['players'][side]
                if not source or len(source)>MAX_SOURCE_BYTES or hashlib.sha256(source).hexdigest()!=player['version']:
                    raise ValueError('bot source hash differs from pinned job')
                target=bot_store.bot_directory(store,player['bot_id'],create=True)
                bot_store.publish_source(target/'versions'/(player['version']+'.py'),source)
            job={**job,'bot_store':str(store)}
            output=root/(job['match_id']+'.json')
            if runner is None:
                local_worker._run_job(job,output,box_ids=box_ids)
            else:
                runner(job,output)
            if lost.is_set(): return {'state':'lease_rejected','match_id':job['match_id']}
            raw,record=read_match(root,job['match_id'])
            if len(raw)>MAX_LOG_BYTES: raise ValueError('result exceeds replay size limit')
            local_worker._validated_result(job,output)
            cluster._replay(job,record)
            _save(directory/'outbox.json',{'action':'complete',**lease,'record':record})
    except Exception:
        if not lost.is_set():
            _save(directory/'outbox.json',{'action':'fail',**lease,
                  'message':'Máy chấm gặp lỗi hệ thống. Kiểm tra Isolate, quyền tệp và cấu hình tài nguyên.'})
        return {'state':'worker_error','match_id':job['match_id']}
    finally:
        stopped.set();beat.join(21)
    return {'state':'result_pending','match_id':job['match_id']}


def run_loop(config,directory,*,stop_event=None,interval=2.0,max_jobs=None,runner=None,emit=None):
    with local_worker._host_gate():
        return run_client(load_config(config),directory,stop_event=stop_event,interval=interval,
                          max_jobs=max_jobs,runner=runner,emit=emit)


def run_client(cfg,directory,*,stop_event=None,interval=2.0,max_jobs=None,runner=None,emit=None,box_ids=(0,1)):
    box_ids=validate_box_pair(box_ids)
    directory=elo_store._database_path(Path(directory)/'path-check','Worker directory').parent
    directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
    client=Client(cfg);outbox=directory/'outbox.json'
    stop=stop_event if stop_event is not None else Event()
    count=0;ready=runner is not None;last=None
    while not stop.is_set():
        try:
            if outbox.exists():
                answer=flush(client,outbox)
                if emit: emit(answer)
                if max_jobs is not None and count>=max_jobs: break
            with (local_worker._worker_lock() if box_ids==(0,1) else local_worker._worker_lock(box_ids)):
                if not ready:
                    checked([ISOLATE,'--check-config'])
                    cg=checked([ISOLATE,'--cg','--print-cg-root']).stdout.strip()
                    if not Path(cg).is_dir() or any((BOX_ROOT/str(i)).exists() or (BOX_ROOT/str(i)).is_symlink() for i in box_ids):
                        raise RuntimeError('sandbox unavailable')
                    ready=True
                if runner is None and any((BOX_ROOT/str(i)).exists() or (BOX_ROOT/str(i)).is_symlink() for i in box_ids):
                    raise RuntimeError('sandbox still occupied; inspect retained state before claiming another match')
                assignment=client.call({'action':'claim'})
                if assignment.get('state')=='assigned':
                    if emit: emit({'state':'assigned','match_id':assignment['job']['match_id'],'worker_id':cfg['worker_id']})
                    result=execute(client,assignment,directory,runner=runner,box_ids=box_ids)
                    count+=1
                    if emit: emit(result)
                elif assignment.get('state') not in ('idle','busy'):
                    raise ValueError('unexpected worker assignment')
                elif assignment['state']!=last:
                    if emit: emit({'state':assignment['state'],'worker_id':cfg['worker_id']})
                last=assignment['state']
            if outbox.exists():
                answer=flush(client,outbox)
                if emit: emit(answer)
            if max_jobs is not None and count>=max_jobs: break
        except RPCError as error:
            if emit: emit({'state':'connection_retry','http_status':error.status})
            if error.status in (400,401,403): raise ValueError('worker request or key rejected; inspect retained outbox') from None
        except RuntimeError as error:
            if 'another local Worker' not in str(error): raise
        if stop.wait(interval): break
    return {'state':'stopped','jobs':count,'worker_id':cfg['worker_id']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--directory',type=Path,default=Path('output/remote-worker'))
    parser.add_argument('--max-jobs',type=int)
    args=parser.parse_args()
    try:
        if args.max_jobs is not None and args.max_jobs<1: raise ValueError('invalid max-jobs')
        print(json.dumps(run_loop(args.config,args.directory,max_jobs=args.max_jobs,
              emit=lambda x:print(json.dumps(x,ensure_ascii=False),flush=True))),flush=True)
    except KeyboardInterrupt: print('{"state":"stopped"}')
    except (ValueError,RuntimeError,OSError,KeyError):
        parser.exit(1,'Máy chấm chưa chạy được. Kiểm tra kết nối, khóa riêng và Isolate; giữ nguyên outbox để thử lại.\n')


if __name__=='__main__': main()
