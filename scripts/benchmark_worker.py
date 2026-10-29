"""Compare real Isolate match throughput at several slot counts, without live data."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from ottv2 import bot_store, local_worker, worker_pool
from ottv2.map_config import load_map
from ottv2.protocol import state_message
from ottv2.rules import CURRENT_LIMITS


def benchmark(destination, slots, matches=16):
    if not sys.platform.startswith('linux'):
        raise ValueError('Chạy trong Ubuntu, dừng pool trước khi đo.')
    for value in slots:
        worker_pool.slot_count(value)
    if len(slots) != len(set(slots)) or not 8 <= matches <= 200:
        raise ValueError('Dùng các mức luồng khác nhau, 8..200 trận/mức.')
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Chọn tên kết quả mới; không ghi đè lần đo cũ.')
    reports = []
    # Same global lock as the live pool: cannot steal occupied sandboxes.
    with worker_pool._pool_lock(), TemporaryDirectory(prefix='ottv2-benchmark-') as tmp:
        tmp = Path(tmp)
        store = tmp/'bots'
        versions = {}
        for name in ('depth3-alpha','depth3-guardian'):
            archive = tmp/(name+'.zip')
            with zipfile.ZipFile(archive,'w') as package:
                package.writestr('main.py',(ROOT/'examples'/name/'main.py').read_bytes())
            versions[name] = bot_store.import_zip(store,name,archive)['version']
        game, identity = load_map(ROOT/'maps/nine-piece.json')
        initial = state_message(game)
        for count in slots:
            worker_pool.check_resources(count)
            rows = []
            def run_slot(index):
                values = []
                for number in range(index,matches,count):
                    seed = 60000 + number
                    names = ('depth3-alpha','depth3-guardian') if number%2 == 0 else ('depth3-guardian','depth3-alpha')
                    first = 'A' if number%2 == 0 else 'B'
                    job = {'schema_version':1,'match_id':uuid4().hex,'seed':seed,'first':first,
                        'created_at_utc':datetime.now(timezone.utc).isoformat(),
                        'bot_store':str(store),'map':identity,
                        'initial_state':{**initial,'turn':first,'you':first},'limits':dict(CURRENT_LIMITS),
                        'players':{side:{'bot_id':name,'version':versions[name]} for side,name in zip(('A','B'),names)}}
                    output = tmp/(job['match_id']+'.json')
                    local_worker._run_job(job,output,box_ids=worker_pool.boxes(index))
                    record = json.loads(output.read_bytes())
                    values.append({'seed':seed,'reason':record['result']['reason'],
                        'elapsed':record['elapsed_seconds_including_cleanup'],
                        'cleanup_complete':record['cleanup_complete'],
                        'max_response_seconds':max(r['max_response_wall_seconds'] for r in record['resources'].values())})
                    output.unlink()
                return values
            start = time.monotonic()
            with ThreadPoolExecutor(max_workers=count) as executor:
                for values in executor.map(run_slot, range(count)):
                    rows.extend(values)
            elapsed = time.monotonic()-start
            report = {'slots':count,'matches':matches,'elapsed_seconds':round(elapsed,3),
                'matches_per_hour':round(matches/elapsed*3600,1),
                'outcomes':dict(Counter(r['reason'] for r in rows)),
                'all_cleanup_complete':all(r['cleanup_complete'] for r in rows),
                'max_response_seconds':max(r['max_response_seconds'] for r in rows),'trials':sorted(rows,key=lambda r:r['seed'])}
            reports.append(report)
            print(json.dumps(report,ensure_ascii=False),flush=True)
    summary = {'measured_at_utc':datetime.now(timezone.utc).isoformat(),
        'limits':dict(CURRENT_LIMITS),'scope':'Local sandbox; excludes Internet and VPS load',
        'source_sha256':{n:hashlib.sha256((ROOT/'examples'/n/'main.py').read_bytes()).hexdigest() for n in versions},
        'batches':reports}
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf-8') as stream:
        json.dump(summary,stream,ensure_ascii=False,indent=2)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--slots',type=int,nargs='+',default=[1,2,4])
    parser.add_argument('--matches',type=int,default=16)
    args = parser.parse_args()
    try:
        benchmark(args.output,args.slots,args.matches)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, 'Chưa đo được: '+str(error)+'\n')
