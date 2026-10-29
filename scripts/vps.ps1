param(
    [ValidateSet('connect','upload','download')][string]$Action = 'connect',
    [string]$LocalPath,
    [string]$RemotePath,
    [string]$RemoteCommand,
    [string]$KeyPath = "$env:USERPROFILE\Downloads\ottv2-key.pem"
)
$ErrorActionPreference = 'Stop'
$taskKey = [IO.Path]::GetFullPath($KeyPath).Replace('\','/')
if (-not (Test-Path -LiteralPath $taskKey -PathType Leaf)) { throw 'Không tìm thấy khóa SSH. Dùng -KeyPath để chọn đúng tệp PEM.' }
$taskPrefix = Join-Path ([IO.Path]::GetTempPath()) ('ottv2-vps-' + [guid]::NewGuid().ToString('N'))
$taskConfig = $taskPrefix + '.config'
$taskKnown = ($taskPrefix + '.known-hosts').Replace('\','/')
# Public host keys verified through Azure / the already trusted old connection.
$taskPins = @'
ottv2-central-v2 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPfVkWd/m0s+ut4bJNUMjQduPWLPNUDtbe5nOhMJelhW
ottv2-old-vps ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF31IUOtcsi27GixvhCWTRethdBxN06lCtxe5FB+uRlm
'@
$taskSettings = @"
Host ottv2
    HostName 172.16.0.5
    User azureuser
    IdentityFile "$taskKey"
    IdentitiesOnly yes
    HostKeyAlias ottv2-central-v2
    UserKnownHostsFile "$taskKnown"
    StrictHostKeyChecking yes
    ConnectTimeout 15
    ProxyCommand ssh.exe -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=ottv2-old-vps -o "UserKnownHostsFile=$taskKnown" -i "$taskKey" -W %h:%p azureuser@20.214.142.35
"@
try {
    [IO.File]::WriteAllText($taskKnown, $taskPins + "`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($taskConfig, $taskSettings + "`n", [Text.UTF8Encoding]::new($false))
    if ($Action -eq 'connect') {
        if ($RemoteCommand) { & ssh.exe -F $taskConfig ottv2 $RemoteCommand }
        else { & ssh.exe -F $taskConfig ottv2 }
    } else {
        if (-not $LocalPath -or -not $RemotePath) { throw 'Cần cả -LocalPath và -RemotePath.' }
        $taskLocal = [IO.Path]::GetFullPath($LocalPath)
        if ($Action -eq 'upload') {
            if (-not (Test-Path -LiteralPath $taskLocal -PathType Leaf)) { throw 'Không có tệp cần chuyển.' }
            & scp.exe -F $taskConfig $taskLocal ('ottv2:' + $RemotePath)
        } else {
            if (Test-Path -LiteralPath $taskLocal) { throw 'Tệp đích đã có. Dùng tệp hiện có nếu đúng, hoặc chọn tên tải xuống mới.' }
            & scp.exe -F $taskConfig ('ottv2:' + $RemotePath) $taskLocal
        }
    }
    $taskExitCode = $LASTEXITCODE
} finally {
    foreach ($taskFile in @($taskConfig, $taskKnown)) {
        if (Test-Path -LiteralPath $taskFile) { Remove-Item -LiteralPath $taskFile -Force }
    }
}
exit $taskExitCode
