"""Bounded /api/status benchmark against a separate synthetic loopback server."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import Request, urlopen

ROOT=Path(os.environ.get('OTTV2_BENCHMARK_SOURCE',Path(__file__).resolve().parent.parent)).resolve()
sys.path.insert(0,str(ROOT))
from scripts.load_test_server import seed

def get(url,client=None):
    headers={'Host':'127.0.0.1:18765'} if url.startswith('http://127.0.0.1:18765/') else {}
    if client:headers['Cookie']=client['cookie']
    start=time.monotonic()
    try:
        with urlopen(Request(url,headers=headers),timeout=5) as response:
            body=json.load(response);status=response.status
    except Exception as error:
        return {'status':str(getattr(error,'code','network_error')),'seconds':time.monotonic()-start,'body':{}}
    return {'status':str(status),'seconds':time.monotonic()-start,'body':body}

def run(destination,students=4000):
    if not sys.platform.startswith('linux') or not 2<=students<=4000:
        raise ValueError('Use Linux and 2..4000 synthetic students.')
    destination=Path(destination)
    if destination.exists():raise FileExistsError('Choose a new output file.')
    # Refuse to connect to any pre-existing service.
    with socket.socket() as probe:probe.bind(('127.0.0.1',18765))
    phases=[]
    with TemporaryDirectory(prefix='ottv2-api-measure-') as temporary:
        fixture=Path(temporary)/'fixture';mask=os.umask(0o077)
        try:seed(fixture,students)
        finally:os.umask(mask)
        clients=json.loads((fixture/'clients.private.json').read_text())['clients']
        # Queue data is synthetic; no worker, Google or real tournament database.
        from ottv2 import match_queue
        for client in clients:match_queue.find_match(fixture/'queue.sqlite3',client['identity'])
        with (Path(temporary)/'server.log').open('w') as log:
            server=subprocess.Popen([sys.executable,str(ROOT/'scripts/load_test_server.py'),'serve','--directory',str(fixture),'--port','18765'],cwd=ROOT,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    if server.poll() is not None:raise RuntimeError('Synthetic server exited.')
                    before=get('http://127.0.0.1:18765/_loadtest')
                    if before['status']=='200':break
                    time.sleep(.05)
                else:raise RuntimeError('Synthetic server not ready.')
                for rate,count in ((25,500),(100,2000),(200,4000)):
                    rows=[];health=[];start=time.monotonic()
                    with ThreadPoolExecutor(max_workers=64) as executor:
                        pending=[]
                        for index in range(count):
                            time.sleep(max(0,start+index/rate-time.monotonic()))
                            if index%rate==0:
                                check=get('https://ottv2-nhom12.koreacentral.cloudapp.azure.com/health')
                                health.append({'status':check['status'],'seconds':check['seconds']})
                                if check['status']!='200':raise RuntimeError('Production health failed; benchmark stopped.')
                            pending.append(executor.submit(get,'http://127.0.0.1:18765/api/status',clients[index%students]))
                        rows=[future.result() for future in pending]
                    elapsed=time.monotonic()-start;times=sorted(r['seconds'] for r in rows)
                    after=get('http://127.0.0.1:18765/_loadtest')['body']
                    phase={'offered_rps':rate,'requests':count,'elapsed_seconds':round(elapsed,3),'achieved_rps':round(count/elapsed,3),'status_counts':dict(Counter(r['status'] for r in rows)),
                        'identity_mismatches':sum(r['status']=='200' and r['body'].get('user',{}).get('identity')!=clients[i%students]['identity'] for i,r in enumerate(rows)),
                        'p50_ms':round(times[math.ceil(len(times)*.5)-1]*1000,3),'p95_ms':round(times[math.ceil(len(times)*.95)-1]*1000,3),'max_ms':round(max(times)*1000,3),
                        'application_peak_rss_kib':after['max_rss_kib'],'application_cpu_seconds':round(after['cpu_seconds']-before['body']['cpu_seconds'],3),'production_health':health}
                    phases.append(phase);before={'body':after}
                    print(json.dumps({'state':'phase_completed',**{k:v for k,v in phase.items() if k!='production_health'}}),flush=True)
                    if phase['status_counts']!={'200':count} or phase['identity_mismatches']:break
            finally:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait()
    result={'measured_at_utc':datetime.now(timezone.utc).isoformat(),'logical_cpus':os.cpu_count(),'synthetic_students':students,'synthetic_pending_jobs':students//2,
        'scope':'Separate generator and synthetic API process on the same VM. Direct HTTP loopback; excludes TLS, Internet, login hashing, Google sync and bot execution. Production website stays active. No real accounts or jobs changed.',
        'phases':phases}
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--students',type=int,default=4000)
    args=parser.parse_args();run(args.output,args.students)
