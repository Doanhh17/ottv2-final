param(
    [string]$Distribution = 'Ubuntu-24.04',
    [string]$LinuxUser = 'doanh',
    [string]$ProjectDirectory = '/home/doanh/ottv2-local',
    [string]$Database = 'output/local-arena/queue.sqlite3',
    [ValidateRange(0, 65535)][int]$Port = 8765,
    [switch]$Once,
    [switch]$Distributed,
    [ValidateRange(1, 8)][int]$WorkerSlots = 8,
    [ValidateSet('shared', 'dedicated')][string]$WorkerCpuMode = 'shared'
)

$ErrorActionPreference = 'Stop'
$bootstrap = @'
from pathlib import Path
import subprocess
import sys
import sqlite3
import json
user, project, database, port, once, distributed, slots, cpu_mode = sys.argv[1:]
if not Path(project).is_dir():
    raise SystemExit('Project directory does not exist: ' + project)
subprocess.run(['systemctl', 'start', 'isolate.service'], check=True)
command = ['runuser', '-u', user, '--', 'python3', '-m', 'ottv2.local_arena', '--db', database, '--port', port]
account_db = str(Path(database).parent / 'accounts.sqlite3')
if not (Path(project) / account_db).is_file():
    raise SystemExit('Accounts are not prepared. Run: python3 -m ottv2.prepare_local --demo')
command.extend(['--accounts', account_db])
google_client = str(Path(database).parent / 'google-client.json')
if (Path(project) / google_client).is_file():
    command.extend(['--google-client', google_client])
if once == '1':
    command.extend(['--max-cycles', '1'])
with sqlite3.connect('file:' + str(Path(project) / database) + '?mode=ro',uri=True) as queue:
    mode = json.loads(queue.execute('SELECT data FROM config WHERE id=1').fetchone()[0]).get('execution_mode','local')
if distributed == '1' or mode == 'distributed':
    command.append('--distributed')
    pool = Path(project) / 'output/private-workers/worker-local-pool.json'
    worker = Path(project) / 'output/private-workers/worker-local.json'
    if pool.is_file():
        command.extend(['--worker-pool-config',str(pool),'--worker-slots',slots,'--worker-cpu-mode',cpu_mode])
    elif worker.is_file():
        command.extend(['--worker-config',str(worker)])
raise SystemExit(subprocess.call(command, cwd=project))
'@
$singleCycle = if ($Once) { '1' } else { '0' }
$distributedMode = if ($Distributed) { '1' } else { '0' }
& wsl.exe -d $Distribution -u root --exec python3 -c $bootstrap $LinuxUser $ProjectDirectory $Database ([string]$Port) $singleCycle $distributedMode ([string]$WorkerSlots) $WorkerCpuMode
exit $LASTEXITCODE
