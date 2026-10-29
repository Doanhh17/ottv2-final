param(
    [ValidateSet('worker_local','worker_sinh','worker_hai','worker_duong')]
    [string]$Name = 'worker_local',
    [string]$DesktopPath = [Environment]::GetFolderPath('Desktop')
)
$ErrorActionPreference = 'Stop'
$launchers = @{
    worker_local = 'start-worker.cmd'
    worker_sinh = 'worker_sinh.cmd'
    worker_hai = 'worker_hai.cmd'
    worker_duong = 'worker_duong.cmd'
}
$sourcePath = Join-Path $PSScriptRoot $launchers[$Name]
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) { throw 'Khong tim thay tep bat may cham trong goi nguon.' }
if (-not (Test-Path -LiteralPath $DesktopPath -PathType Container)) { throw 'Khong tim thay thu muc Desktop.' }
$targetPath = Join-Path ([IO.Path]::GetFullPath($DesktopPath)) ('OTTv2 - ' + $Name + '.cmd')
Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
Write-Output ('DA TAO NUT DESKTOP: ' + $targetPath)
Write-Output 'Nhung lan sau chi can bam dup tep nay. Giu Windows thuc khi cham.'
