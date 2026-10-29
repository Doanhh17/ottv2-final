"""Start the HTTP portal/API and the isolated match runner with one Ubuntu command."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from threading import Event, Thread

from . import elo_store, local_api, local_runner, local_worker, match_queue


def run_arena(database, results=None, *, port=8765, interval=2.0, max_cycles=None,
              stop_event=None, runner=None, emit=None, account_db=None, google_client=None, public_origin=None,
              distributed=False, worker_config=None, worker_directory=None,
              worker_pool_config=None, worker_slots=None, worker_cpu_mode='shared'):
    database = elo_store._database_path(database, 'Queue')
    if not database.exists():
        raise ValueError('initialize the queue first')
    with closing(elo_store._connect(database, readonly=True)) as connection:
        match_queue._schema(connection)
        config = match_queue._config(connection)
    results = Path(results).absolute() if results is not None else database.parent / 'matches'
    if config.get('execution_mode')=='distributed' and not distributed:
        raise ValueError('queue uses distributed mode; add --distributed or explicitly switch to local mode')
    coordinator=None
    if worker_config is not None and worker_pool_config is not None:
        raise ValueError('choose a single Worker or a Worker pool')
    if worker_slots is not None and worker_pool_config is None:
        raise ValueError('worker-slots requires worker-pool-config')
    if (worker_config is not None or worker_pool_config is not None) and not distributed:
        raise ValueError('embedded worker requires --distributed')
    if distributed:
        if account_db is None: raise ValueError('distributed mode requires accounts')
        from . import cluster
        if worker_config is not None:
            from . import remote_worker
            remote_worker.load_config(worker_config)
        if worker_pool_config is not None:
            from . import worker_pool
            pool = worker_pool.load_config(worker_pool_config)
            if worker_cpu_mode not in ('shared','dedicated'):
                raise ValueError('invalid worker CPU mode')
            if worker_slots is not None and worker_pool.slot_count(worker_slots) > len(pool['workers']):
                raise ValueError('worker slots exceed registered pool')
    if google_client is not None and account_db is None:
        raise ValueError('Google sync requires accounts')
    if public_origin is not None and account_db is None:
        raise ValueError('public HTTPS mode requires accounts')
    maintenance = None
    if account_db is None:
        server = local_api.create_server(config['store'], port, results, database)
    else:
        from . import portal_api, portal_data
        server = portal_api.create_server(config['store'], port, results, database, account_db, public_origin=public_origin)
        maintenance = lambda: portal_data.process_update(database, account_db)
    # Validate public credentials/origin and reserve the HTTP port before changing
    # the persisted execution mode. Failed startup must not switch a local queue.
    try:
        if distributed:
            if config.get('execution_mode')!='distributed':
                with local_worker._worker_lock(): cluster.enable(database)
            else:
                cluster.enable(database)
            coordinator=cluster.Coordinator(database,results)
            with closing(elo_store._connect(database,readonly=True)) as connection:
                config=match_queue._config(connection)
        server.coordinator=coordinator
    except BaseException:
        server.server_close()
        raise
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    google_stop, google_thread = Event(), None
    worker_stop, worker_thread = Event(), None
    if google_client is not None:
        from . import google_sync
        def synchronize():
            try:
                google_sync.run_loop(account_db, config['ratings'], google_client, google_stop, emit=emit)
            except (ValueError, OSError, sqlite3.Error):
                if emit:
                    emit({'state': 'google_disabled', 'message': 'Kiểm tra tệp cấu hình kết nối Google.'})
        google_thread = Thread(target=synchronize, daemon=True)
        google_thread.start()
    try:
        if worker_config is not None or worker_pool_config is not None:
            from . import remote_worker
            def work():
                try:
                    if worker_pool_config is not None:
                        from . import worker_pool
                        worker_pool.run_loop(worker_pool_config,worker_directory or database.parent/'worker-pool',
                                             slots=worker_slots,stop_event=worker_stop,emit=emit,
                                             cpu_mode=worker_cpu_mode)
                    else:
                        remote_worker.run_loop(worker_config,worker_directory or database.parent/'local-worker',
                                               stop_event=worker_stop,emit=emit)
                except (ValueError,RuntimeError,OSError,KeyError):
                    if emit: emit({'state':'worker_disabled','message':'Kiểm tra khóa máy chấm và Isolate.'})
            worker_thread=Thread(target=work,daemon=True)
            worker_thread.start()
        if emit is not None:
            emit({'state': 'started', 'url': f'http://127.0.0.1:{server.server_port}',
                  'mode': 'local_accounts' if account_db is not None else 'local_read_only'})
        if distributed:
            return cluster.run_loop(coordinator,interval=interval,max_cycles=max_cycles,
                                    stop_event=stop_event,maintenance=maintenance,emit=emit)
        return local_runner.run_loop(database, results, interval=interval, max_cycles=max_cycles,
                                      stop_event=stop_event, runner=runner, emit=emit, maintenance=maintenance)
    finally:
        worker_stop.set()
        if worker_thread is not None:
            worker_thread.join(330)
        google_stop.set()
        if google_thread is not None:
            google_thread.join(2)
        server.shutdown()
        thread.join(3)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('output/queue.sqlite3'))
    parser.add_argument('--results', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--interval', type=float, default=2.0)
    parser.add_argument('--max-cycles', type=int)
    parser.add_argument('--accounts', type=Path)
    parser.add_argument('--google-client', type=Path)
    parser.add_argument('--public-origin', help='HTTPS origin served by a local reverse proxy; backend stays loopback')
    parser.add_argument('--distributed',action='store_true',help='dispatch matches to authenticated remote workers')
    parser.add_argument('--worker-config',type=Path,help='optionally run one authenticated worker on this host')
    parser.add_argument('--worker-directory',type=Path)
    parser.add_argument('--worker-pool-config',type=Path,help='run up to 12 match slots on this host')
    parser.add_argument('--worker-slots',type=int,help='number of enabled pool slots, from 1 to 12')
    parser.add_argument('--worker-cpu-mode',choices=('shared','dedicated'),default='shared')
    args = parser.parse_args()
    if args.google_client is None and args.accounts is not None:
        candidate = args.accounts.parent / 'google-client.json'
        if candidate.is_file():
            args.google_client = candidate
    try:
        def emit(answer):
            print(json.dumps(answer, ensure_ascii=False, allow_nan=False), flush=True)
        emit(run_arena(args.db, args.results, port=args.port, interval=args.interval,
                       max_cycles=args.max_cycles, emit=emit, account_db=args.accounts, google_client=args.google_client,
                       public_origin=args.public_origin,distributed=args.distributed,
                       worker_config=args.worker_config,worker_directory=args.worker_directory,
                       worker_pool_config=args.worker_pool_config,worker_slots=args.worker_slots,
                       worker_cpu_mode=args.worker_cpu_mode))
    except KeyboardInterrupt:
        print(json.dumps({'state': 'stopped', 'reason': 'keyboard_interrupt'}), flush=True)
    except (ValueError, KeyError, OSError, RuntimeError, OverflowError, sqlite3.Error) as error:
        parser.exit(1, f'LOCAL ARENA ERROR: {error}\n')


if __name__ == '__main__':
    main()
