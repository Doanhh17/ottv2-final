"""One host, up to 12 independently authenticated Isolate match slots."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from queue import SimpleQueue
import re
import secrets
import sqlite3
import sys
from threading import Event, Thread

from . import bot_store, cluster, elo_store, local_worker, match_queue, remote_worker
from .isolated_session import ISOLATE, checked
from .map_config import reject_duplicates

MAX_SLOTS = 12
PROFILE_PATH = Path.home() / '.config' / 'ottv2' / 'worker-profile.json'


def load_profile(host_id):
    if not PROFILE_PATH.exists():
        return None
    if PROFILE_PATH.is_symlink() or PROFILE_PATH.is_junction() or PROFILE_PATH.stat().st_size > 4096:
        raise ValueError('Tệp cấu hình máy chấm chưa đúng.')
    data = json.loads(PROFILE_PATH.read_text(encoding='utf-8'), object_pairs_hook=reject_duplicates)
    if not isinstance(data, dict) or set(data) != {'schema_version','host_id','display_name','max_slots'} or type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('Tệp cấu hình máy chấm chưa đúng.')
    bot_store.validate_bot_id(data['display_name'])
    if data['host_id'] != host_id:
        raise ValueError('Cấu hình số luồng thuộc máy chấm khác; không dùng nhầm khóa.')
    slot_count(data['max_slots'])
    return data


def configure_profile(config, display_name, max_slots):
    cfg = load_config(config)
    bot_store.validate_bot_id(display_name)
    slot_count(max_slots)
    if max_slots > len(cfg['workers']):
        raise ValueError('Số luồng vượt bộ khóa của máy.')
    data = {'schema_version':1,'host_id':cfg['host_id'],'display_name':display_name,'max_slots':max_slots}
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    remote_worker._save(PROFILE_PATH, data)
    return data


def set_label(database, host_id, display_name, max_slots=None):
    bot_store.validate_bot_id(host_id)
    bot_store.validate_bot_id(display_name)
    if max_slots is not None:
        slot_count(max_slots)
    with cluster._LOCK, match_queue._write(database) as db:
        workers = [r[0] for r in db.execute('SELECT worker_id FROM cluster_workers')]
        if not any(w.rpartition('-')[0] == host_id and w.rpartition('-')[2].isdigit() for w in workers):
            raise ValueError('Chưa đăng ký máy chấm này.')
        cfg = match_queue._config(db)
        cfg.setdefault('worker_labels', {})[host_id] = display_name
        if max_slots is not None:
            cfg.setdefault('worker_slot_limits', {})[host_id] = max_slots
        db.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(cfg),))
    return {'host_id':host_id, 'display_name':display_name}


def slot_count(value):
    if type(value) is not int or not 1 <= value <= MAX_SLOTS:
        raise ValueError('Số trận song song phải từ 1 đến 12.')
    return value


def boxes(slot):
    if type(slot) is not int:
        raise ValueError('Chỉ số luồng phải là số nguyên.')
    slot_count(slot + 1)
    return 2 + 2 * slot, 3 + 2 * slot


def load_config(path):
    path = elo_store._database_path(path, 'Worker pool credential')
    if path.stat().st_size > 32768 or os.name != 'nt' and path.stat().st_mode & 0o077:
        raise ValueError('Tệp khóa pool cần quyền 600.')
    cfg = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=reject_duplicates)
    if not isinstance(cfg, dict) or set(cfg) != {'version','host_id','url','workers'} or type(cfg['version']) is not int or cfg['version'] != 1:
        raise ValueError('Tệp cấu hình pool chưa đúng.')
    bot_store.validate_bot_id(cfg['host_id'])
    cfg['url'] = cluster.master_url(cfg['url'])
    if not isinstance(cfg['workers'], list):
        raise ValueError('Thiếu danh sách luồng chấm.')
    slot_count(len(cfg['workers']))
    tokens = set()
    for index, worker in enumerate(cfg['workers']):
        if (not isinstance(worker, dict) or set(worker) != {'worker_id','token'}
                or worker['worker_id'] != f"{cfg['host_id']}-{index+1:02d}"
                or not isinstance(worker['token'], str) or not re.fullmatch('[a-f0-9]{64}', worker['token'])
                or worker['token'] in tokens):
            raise ValueError('Khóa hoặc ID luồng chấm chưa đúng.')
        bot_store.validate_bot_id(worker['worker_id'])
        tokens.add(worker['token'])
    return cfg


def register(database, host_id, url, destination, *, slots=8):
    slot_count(slots)
    bot_store.validate_bot_id(host_id)
    if len(host_id) > 37:
        raise ValueError('ID máy tối đa 37 ký tự để thêm số luồng.')
    url = cluster.master_url(url)
    target = elo_store._database_path(destination, 'Worker pool credential')
    if target.exists():
        raise FileExistsError('Tệp khóa đã có; không ghi đè.')
    cfg = {'version':1, 'host_id':host_id, 'url':url,
           'workers':[{'worker_id':f'{host_id}-{i+1:02d}', 'token':secrets.token_hex(32)} for i in range(slots)]}
    cluster.initialize(database)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with cluster._LOCK, match_queue._write(database) as db:
            for worker in cfg['workers']:
                db.execute('INSERT INTO cluster_workers(worker_id,token_hash) VALUES (?,?)',
                           (worker['worker_id'], hashlib.sha256(worker['token'].encode()).hexdigest()))
            with target.open('x', encoding='utf-8') as stream:
                created = True
                target.chmod(0o600)
                json.dump(cfg, stream)
                stream.flush()
                os.fsync(stream.fileno())
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    return {'state':'pool_registered', 'host_id':host_id, 'slots':slots, 'credential_file':str(target)}


def extend(database, config, slots):
    """Append slot keys atomically; preserve every previous token and outbox ID."""
    slot_count(slots)
    cfg = load_config(config)
    previous = len(cfg['workers'])
    if slots < previous:
        raise ValueError('Chỉ thêm khóa; giảm luồng bằng configure/label, không xóa khóa.')
    changed = False
    try:
        with cluster._LOCK, match_queue._write(database) as db:
            for worker in cfg['workers']:
                row = db.execute('SELECT token_hash,enabled FROM cluster_workers WHERE worker_id=?',
                                 (worker['worker_id'],)).fetchone()
                if row is None or row['token_hash'] != hashlib.sha256(worker['token'].encode()).hexdigest() or not row['enabled']:
                    raise ValueError('Bộ khóa không khớp máy chủ hoặc đã bị thu hồi.')
            workers = list(cfg['workers'])
            for i in range(previous,slots):
                worker = {'worker_id':f"{cfg['host_id']}-{i+1:02d}",'token':secrets.token_hex(32)}
                db.execute('INSERT INTO cluster_workers(worker_id,token_hash) VALUES (?,?)',
                           (worker['worker_id'],hashlib.sha256(worker['token'].encode()).hexdigest()))
                workers.append(worker)
            if slots > previous:
                remote_worker._save(Path(config), {**cfg,'workers':workers})
                changed = True
    except BaseException:
        if changed:
            remote_worker._save(Path(config),cfg)
        raise
    return {'state':'pool_extended','host_id':cfg['host_id'],'previous_slots':previous,
            'slots':slots,'existing_keys_preserved':True}


def revoke(database, config):
    cfg = load_config(config)
    cluster.initialize(database)
    with cluster._LOCK, match_queue._write(database) as db:
        for worker in cfg['workers']:
            row = db.execute('SELECT token_hash FROM cluster_workers WHERE worker_id=?', (worker['worker_id'],)).fetchone()
            if row is None or row['token_hash'] != hashlib.sha256(worker['token'].encode()).hexdigest():
                raise ValueError('Tệp khóa không khớp registry; chưa thu hồi luồng nào.')
        for worker in cfg['workers']:
            db.execute('UPDATE cluster_workers SET enabled=0 WHERE worker_id=?', (worker['worker_id'],))
            db.execute("UPDATE cluster_leases SET expires_at=0 WHERE worker_id=? AND state='active'", (worker['worker_id'],))
    return {'state':'pool_revoked', 'host_id':cfg['host_id'], 'slots':len(cfg['workers'])}


def _physical_cpus():
    cores = {}
    for cpu in sorted(os.sched_getaffinity(0)):
        root = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key = ((root/'physical_package_id').read_text().strip(), (root/'core_id').read_text().strip())
        cores.setdefault(key, cpu)
    return list(cores.values())


def _available_memory_mib():
    text = Path('/proc/meminfo').read_text()
    return int(re.search(r'^MemAvailable:\s+(\d+) kB$', text, re.M)[1]) // 1024


def _cgroup_headroom():
    root = Path('/sys/fs/cgroup').resolve()
    membership = Path('/proc/self/cgroup').read_text().splitlines()
    relative = next((line[3:] for line in membership if line.startswith('0::')),None)
    if relative is None:
        raise ValueError('Pool cần cgroup v2 trên máy chấm.')
    current = (root/relative.lstrip('/')).resolve()
    if not current.is_relative_to(root):
        raise ValueError('Đường dẫn cgroup không hợp lệ.')
    cpus, memory = None, None
    for folder in (current,*current.parents):
        if not folder.is_relative_to(root):break
        cpu = folder/'cpu.max'
        if cpu.exists():
            quota,period = cpu.read_text().split()
            if quota != 'max':
                value = int(quota)/int(period)
                cpus = value if cpus is None else min(cpus,value)
        maximum,used = folder/'memory.max',folder/'memory.current'
        if maximum.exists() and used.exists() and maximum.read_text().strip() != 'max':
            value = max(0,(int(maximum.read_text())-int(used.read_text()))//(1024*1024))
            memory = value if memory is None else min(memory,value)
    return cpus,memory


def resource_plan(slots, *, cpu_mode='shared'):
    slot_count(slots)
    if cpu_mode not in ('shared','dedicated'):
        raise ValueError('Chế độ CPU phải là shared hoặc dedicated.')
    if not sys.platform.startswith('linux'):
        raise ValueError('Chạy máy chấm bên trong Ubuntu/Linux.')
    cpus = _physical_cpus()
    cpu_limit,memory_limit = _cgroup_headroom()
    # Sandbox IDs must be separate; their CPU assignments may be shared.
    box_count = 2 * slots + 2
    needed = box_count if cpu_mode == 'dedicated' else 1
    # Besides the two 64 MiB bot budgets, allow per-slot source/JSON buffers.
    memory = slots * 192 + 512
    available_memory = _available_memory_mib()
    if memory_limit is not None:
        available_memory = min(available_memory,memory_limit)
    if len(cpus) < needed or cpu_limit is not None and cpu_limit < needed or available_memory < memory:
        raise ValueError(f'Mức {slots} trận, CPU {cpu_mode}, cần ít nhất {needed} lõi CPU khả dụng và {memory} MiB RAM trống. Giảm --slots hoặc đổi cấu hình máy.')
    return {'slots':slots, 'cpu_mode':cpu_mode, 'detected_physical_cores':len(cpus),
            'required_cpu_cores':needed, 'required_available_memory_mib':memory,
            'cpu_shared':cpu_mode == 'shared' and len(cpus) < box_count,
            'box_cpus':{i:cpus[i % len(cpus)] for i in range(box_count)}}


def render_isolate_config(slots, destination, *, source=Path('/usr/local/etc/isolate'), cpu_mode='shared'):
    plan = resource_plan(slots,cpu_mode=cpu_mode)
    target = elo_store._database_path(destination, 'Isolate configuration')
    original = Path(source).read_text()
    lines = []
    for line in original.splitlines():
        pin = re.match(r'^\s*box([0-9]+)\.cpus\s*=', line)
        if pin is None or int(pin[1]) not in plan['box_cpus']:
            lines.append(line)
    lines.extend(f'box{box}.cpus = {cpu}' for box,cpu in plan['box_cpus'].items())
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(lines)+'\n')
    return {**plan, 'configuration_file':str(target)}


def check_resources(slots, *, cpu_mode='shared'):
    plan = resource_plan(slots,cpu_mode=cpu_mode)
    checked([ISOLATE, '--check-config'])
    text = Path('/usr/local/etc/isolate').read_text()
    pins = {}
    for box,cpu in re.findall(r'^\s*box(\d+)\.cpus\s*=\s*(\S+)\s*$', text, re.M):
        if int(box) in pins:
            raise ValueError('Trùng cấu hình CPU của sandbox.')
        pins[int(box)] = cpu
    if any(pins.get(box) != str(cpu) for box,cpu in plan['box_cpus'].items()):
        raise ValueError('CPU của sandbox chưa khớp chế độ đã chọn; chạy worker_pool plan với cùng --cpu-mode rồi cài cấu hình Isolate.')
    return plan


@contextmanager
def _pool_lock():
    with local_worker._host_gate(pool=True):
        yield


def run_loop(config, directory, *, slots=None, stop_event=None, interval=2.0,
             max_jobs_per_slot=None, runner=None, emit=None, cpu_mode='shared'):
    cfg = load_config(config)
    count = len(cfg['workers']) if slots is None else slot_count(slots)
    profile = load_profile(cfg['host_id'])
    if profile is not None:
        count = min(count, profile['max_slots'])
    if count > len(cfg['workers']):
        raise ValueError('Số luồng vượt số khóa được cấp.')
    if cpu_mode not in ('shared','dedicated'):
        raise ValueError('Chế độ CPU phải là shared hoặc dedicated.')
    directory = elo_store._database_path(Path(directory)/'path-check', 'Pool directory').parent
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    stop = stop_event if stop_event is not None else Event()
    failures = SimpleQueue()
    results = SimpleQueue()
    def work(index, worker):
        def notify(record):
            if emit:
                emit({**record, 'host_id':cfg['host_id'], 'slot':index+1, 'worker_id':worker['worker_id']})
        try:
            result = remote_worker.run_client({**worker, 'url':cfg['url']}, directory/worker['worker_id'],
                box_ids=boxes(index), stop_event=stop, interval=interval,
                max_jobs=max_jobs_per_slot, runner=runner, emit=notify)
            results.put(result)
        except BaseException as error:
            failures.put(error)
            stop.set()
    with _pool_lock():
        if runner is None:
            check_resources(count,cpu_mode=cpu_mode)
        layout = {'host_id':cfg['host_id'], 'url':cfg['url'],
                  'workers':[w['worker_id'] for w in cfg['workers']]}
        marker = elo_store._database_path(directory/'pool-layout.json', 'Pool layout')
        if marker.exists():
            previous = json.loads(marker.read_text())
            prefix_extension = (isinstance(previous,dict) and set(previous)==set(layout)
                and previous['host_id']==layout['host_id'] and previous['url']==layout['url']
                and isinstance(previous['workers'],list)
                and layout['workers'][:len(previous['workers'])]==previous['workers'])
            if previous != layout and not prefix_extension:
                raise ValueError('Thư mục pool đang giữ dữ liệu của cấu hình khác; không ghi đè.')
        allowed = {'pool-layout.json', *(worker['worker_id'] for worker in cfg['workers'])}
        if any(path.name not in allowed for path in directory.iterdir()):
            raise ValueError('Thư mục có dữ liệu Worker khác; giữ nguyên outbox cũ và dùng thư mục pool riêng.')
        remote_worker._save(marker, layout)
        # Flush all retained results, including slots disabled by a lower limit.
        for worker in cfg['workers']:
            folder = elo_store._database_path(directory/worker['worker_id']/'path-check', 'Slot directory').parent
            if (folder/'outbox.json').exists():
                remote_worker.flush(remote_worker.Client({**worker,'url':cfg['url']}), folder/'outbox.json')
        threads = [Thread(target=work, args=(i,w), name=f'ottv2-slot-{i+1}')
                   for i,w in enumerate(cfg['workers'][:count])]
        started = []
        try:
            for thread in threads:
                thread.start()
                started.append(thread)
            if emit:
                emit({'state':'pool_started','host_id':cfg['host_id'],'display_name':profile['display_name'] if profile else cfg['host_id'],
                      'slots':count,'cpu_mode':cpu_mode})
            while any(thread.is_alive() for thread in started):
                for thread in started:
                    thread.join(0.1)
        finally:
            stop.set()
            for thread in started:
                thread.join()  # Preserve the sandbox lock through cleanup/outbox.
        if not failures.empty():
            raise RuntimeError('Một luồng chấm đã dừng; giữ nguyên thư mục outbox và kiểm kết nối/cấu hình.') from failures.get()
    completed = []
    while not results.empty():
        completed.append(results.get())
    return {'state':'pool_stopped','host_id':cfg['host_id'],'slots':count,
            'jobs':sum(result['jobs'] for result in completed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('register')
    add.add_argument('--db', required=True, type=Path)
    add.add_argument('--host-id', required=True)
    add.add_argument('--url', required=True)
    add.add_argument('--slots', type=int, default=8)
    add.add_argument('--output', required=True, type=Path)
    delete = sub.add_parser('revoke')
    delete.add_argument('--db', required=True, type=Path)
    delete.add_argument('--config', required=True, type=Path)
    expand = sub.add_parser('extend')
    expand.add_argument('--db', required=True, type=Path)
    expand.add_argument('--config', required=True, type=Path)
    expand.add_argument('--slots', required=True, type=int)
    plan = sub.add_parser('plan')
    plan.add_argument('--slots', required=True, type=int)
    plan.add_argument('--output', required=True, type=Path)
    plan.add_argument('--cpu-mode',choices=('shared','dedicated'),default='shared')
    run = sub.add_parser('run')
    run.add_argument('--config', required=True, type=Path)
    run.add_argument('--directory', required=True, type=Path)
    run.add_argument('--slots', type=int)
    run.add_argument('--cpu-mode',choices=('shared','dedicated'),default='shared')
    profile = sub.add_parser('configure')
    profile.add_argument('--config', required=True, type=Path)
    profile.add_argument('--name', required=True)
    profile.add_argument('--max-slots', required=True, type=int)
    label = sub.add_parser('label')
    label.add_argument('--db', required=True, type=Path)
    label.add_argument('--host-id', required=True)
    label.add_argument('--name', required=True)
    label.add_argument('--max-slots', type=int)
    args = parser.parse_args()
    try:
        if args.command == 'label':
            print(json.dumps(set_label(args.db,args.host_id,args.name,args.max_slots)),flush=True)
            return
        if args.command == 'register':
            answer = register(args.db,args.host_id,args.url,args.output,slots=args.slots)
        elif args.command == 'revoke':
            answer = revoke(args.db,args.config)
        elif args.command == 'extend':
            answer = extend(args.db,args.config,args.slots)
        elif args.command == 'plan':
            answer = render_isolate_config(args.slots,args.output,cpu_mode=args.cpu_mode)
        elif args.command == 'configure':
            answer = configure_profile(args.config,args.name,args.max_slots)
        else:
            answer = run_loop(args.config,args.directory,slots=args.slots,cpu_mode=args.cpu_mode,
                              emit=lambda record:print(json.dumps(record,ensure_ascii=False),flush=True))
        print(json.dumps(answer,ensure_ascii=False))
    except KeyboardInterrupt:
        print('{"state":"pool_stopped"}')
    except (ValueError,RuntimeError,OSError,KeyError,TypeError,sqlite3.Error) as error:
        parser.exit(1,f'WORKER POOL: {error}\n')


if __name__ == '__main__':
    main()
