param(
    [string]$ServiceName = "CRM",
    [string]$RootPath = "C:\AppMonitoramento\Monitoramento bakof"
)

$ErrorActionPreference = "Stop"

$nssm = (Get-Command nssm.exe -ErrorAction Stop).Source
$python = Join-Path $RootPath ".venv\Scripts\python.exe"
$appFullPath = Join-Path $RootPath "app.py"
$app = "app.py"
$logs = Join-Path $RootPath "logs"

if (!(Test-Path $python)) { throw "Python da venv nao encontrado: $python" }
if (!(Test-Path $appFullPath)) { throw "app.py nao encontrado: $appFullPath" }
if (!(Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    & sc.exe stop $ServiceName | Out-Null
    Start-Sleep -Seconds 1
    & sc.exe delete $ServiceName | Out-Null
    Start-Sleep -Seconds 1
}

# Evita erro de bind quando ha processo manual ocupando a porta 5000.
$listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    try {
        Stop-Process -Id $listener.OwningProcess -Force -ErrorAction Stop
        Start-Sleep -Seconds 1
    } catch {
        Write-Warning "Nao foi possivel finalizar processo da porta 5000 (PID $($listener.OwningProcess))."
    }
}

& $nssm install $ServiceName $python $app
& $nssm set $ServiceName AppDirectory $RootPath
& $nssm set $ServiceName DisplayName $ServiceName
& $nssm set $ServiceName Description "App Flask Bakof (Waitress)"
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppExit Default Restart
& $nssm set $ServiceName AppStdout (Join-Path $logs "bakof_app.out.log")
& $nssm set $ServiceName AppStderr (Join-Path $logs "bakof_app.err.log")
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateOnline 1
& $nssm set $ServiceName AppRotateBytes 10485760

& sc.exe start $ServiceName | Out-Null
Start-Sleep -Seconds 2
& sc.exe query $ServiceName
