<#
Manage the Cassini BlueFors monitor as Windows services.

  cassini.bat install         Install + start both services and the guard task (admin)
  cassini.bat uninstall       Remove services and guard task (admin)
  cassini.bat start           Start both services (admin)
  cassini.bat stop            Stop both services and pause the guard (admin)
  cassini.bat restart-api     Restart only the API; the public URL is unchanged (admin)
  cassini.bat restart-tunnel  Restart cloudflared; this ISSUES A NEW PUBLIC URL (admin)
  cassini.bat status          Show service state, health and the public URL
  cassini.bat url             Print the public URL and copy it to the clipboard

Services (run as LocalSystem, start at boot, no login needed):
  CassiniAPI     runtime\python + uvicorn: BlueFors log sync + API on 127.0.0.1:8001
  CassiniTunnel  cloudflared quick tunnel -> 127.0.0.1:8001 (metrics on 127.0.0.1:20241)
Guard task "Cassini Service Guard" (SYSTEM, at startup + every 5 min) starts
either service if it is ever left stopped.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'restart-api', 'restart-tunnel', 'status', 'url')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runtime = Join-Path $Repo 'runtime'
$SvcDir = Join-Path $Runtime 'services'
$LogDir = Join-Path $Repo 'logs'
$Python = Join-Path $Runtime 'python\python.exe'
$Cloudflared = Join-Path $Repo 'cloudflared.exe'
$WinSW = Join-Path $Runtime 'bin\WinSW-x64.exe'
$UrlFile = Join-Path $Repo 'CURRENT_PUBLIC_URL.txt'
$DeployLog = Join-Path $LogDir 'deploy.log'
$ApiPort = 8001
$MetricsAddr = '127.0.0.1:20241'
$GuardTask = 'Cassini Service Guard'
$ApiSvc = 'CassiniAPI'
$TunnelSvc = 'CassiniTunnel'
$AdminCommands = @('install', 'uninstall', 'start', 'stop', 'restart-api', 'restart-tunnel')

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Health {
    try { return Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/health" -TimeoutSec 5 } catch { return $null }
}

function Get-QuickTunnelUrl {
    try {
        $r = Invoke-RestMethod -Uri "http://$MetricsAddr/quicktunnel" -TimeoutSec 5
        if ($r.hostname) { return "https://$($r.hostname)" }
    } catch { }
    return $null
}

function Write-ServiceXml([string]$Id, [string]$Name, [string]$Description, [string]$Exe, [string]$Arguments, [string]$WorkDir) {
    $xml = @"
<service>
  <id>$Id</id>
  <name>$Name</name>
  <description>$Description</description>
  <executable>$Exe</executable>
  <arguments>$Arguments</arguments>
  <workingdirectory>$WorkDir</workingdirectory>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="5 sec"/>
  <onfailure action="restart" delay="15 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>20 sec</stoptimeout>
  <env name="PYTHONUNBUFFERED" value="1"/>
  <env name="PYTHONUTF8" value="1"/>
  <logpath>$LogDir</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>5</keepFiles>
  </log>
</service>
"@
    Copy-Item $WinSW (Join-Path $SvcDir "$Id.exe") -Force
    Set-Content -Path (Join-Path $SvcDir "$Id.xml") -Value $xml -Encoding UTF8
}

function Invoke-WinSW([string]$Id, [string]$Verb) {
    $exe = Join-Path $SvcDir "$Id.exe"
    if (Test-Path $exe) { & $exe $Verb | Out-Host }
}

function Remove-CassiniService([string]$Id) {
    if (Get-Service $Id -ErrorAction SilentlyContinue) {
        Write-Host "Removing existing service $Id"
        Stop-Service $Id -Force -ErrorAction SilentlyContinue
        (Get-Service $Id).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
        Invoke-WinSW $Id 'uninstall'
        if (Get-Service $Id -ErrorAction SilentlyContinue) { sc.exe delete $Id | Out-Host }
        Start-Sleep -Seconds 2
    }
}

function Register-Guard {
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c sc.exe start $ApiSvc & sc.exe start $TunnelSvc"
    $atStartup = New-ScheduledTaskTrigger -AtStartup
    $every5 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $GuardTask -Action $action -Trigger $atStartup, $every5 -Principal $principal `
        -Settings $settings -Description 'Starts the Cassini BlueFors monitor services if they are ever left stopped.' -Force | Out-Null
    Write-Host "Registered scheduled task '$GuardTask'"
}

function Wait-ApiHealthy([int]$Seconds = 120) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $h = Get-Health
        if ($h) { return $h }
        Start-Sleep -Seconds 2
    }
    return $null
}

function Wait-TunnelUrl([int]$Seconds = 120) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $u = Get-QuickTunnelUrl
        if ($u) { return $u }
        Start-Sleep -Seconds 2
    }
    return $null
}

function Install-Cassini {
    foreach ($f in @($Python, $Cloudflared, $WinSW)) {
        if (-not (Test-Path $f)) { throw "Missing required file: $f" }
    }
    New-Item -ItemType Directory -Force $SvcDir, $LogDir | Out-Null

    $existingTask = Get-ScheduledTask -TaskName $GuardTask -ErrorAction SilentlyContinue
    if ($existingTask) { Disable-ScheduledTask -TaskName $GuardTask | Out-Null }
    Remove-CassiniService $TunnelSvc
    Remove-CassiniService $ApiSvc

    Write-ServiceXml $ApiSvc 'Cassini BlueFors API' `
        'Mirrors the BlueFors logs into DuckDB and serves the Cassini dashboard API on 127.0.0.1:8001.' `
        $Python "-m uvicorn app.api:app --host 127.0.0.1 --port $ApiPort --workers 1 --timeout-keep-alive 30" `
        (Join-Path $Repo 'backend')
    Write-ServiceXml $TunnelSvc 'Cassini Cloudflare Tunnel' `
        'Cloudflare quick tunnel that publishes the Cassini API (127.0.0.1:8001) for the Streamlit dashboard.' `
        $Cloudflared "tunnel --no-autoupdate --metrics $MetricsAddr --loglevel info --url http://127.0.0.1:$ApiPort" `
        $Repo

    foreach ($id in @($ApiSvc, $TunnelSvc)) {
        Invoke-WinSW $id 'install'
        # Apply the restart actions even when the process exits with an error code.
        sc.exe failureflag $id 1 | Out-Null
    }

    Write-Host "Starting $ApiSvc ..."
    Start-Service $ApiSvc
    $h = Wait-ApiHealthy 120
    if ($h) { Write-Host "API healthy: rows=$($h.rows) latest=$($h.latest_ts_eastern)" } else { Write-Warning 'API did not answer /health within 120 s; check logs\CassiniAPI.err.log' }

    Write-Host "Starting $TunnelSvc ..."
    Start-Service $TunnelSvc
    $url = Wait-TunnelUrl 120
    if ($url) {
        Set-Content -Path $UrlFile -Value $url -Encoding ASCII
        Write-Host ''
        Write-Host "PUBLIC API URL: $url"
        Write-Host 'Set this as api_base in the Streamlit app secrets.'
    } else {
        Write-Warning 'cloudflared did not report a quick-tunnel URL within 120 s; check logs\CassiniTunnel.err.log'
    }

    Register-Guard
}

function Uninstall-Cassini {
    if (Get-ScheduledTask -TaskName $GuardTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $GuardTask -Confirm:$false
        Write-Host "Removed scheduled task '$GuardTask'"
    }
    Remove-CassiniService $TunnelSvc
    Remove-CassiniService $ApiSvc
}

function Show-Status {
    Write-Host '--- services ---'
    foreach ($id in @($ApiSvc, $TunnelSvc)) {
        $s = Get-Service $id -ErrorAction SilentlyContinue
        if ($s) { Write-Host ("{0,-14} {1,-8} start={2}" -f $id, $s.Status, $s.StartType) } else { Write-Host ("{0,-14} NOT INSTALLED" -f $id) }
    }
    $t = Get-ScheduledTask -TaskName $GuardTask -ErrorAction SilentlyContinue
    if ($t) { Write-Host ("{0,-14} {1}" -f 'Guard task', $t.State) }
    elseif (Test-Admin) { Write-Host 'Guard task     NOT INSTALLED' }
    else { Write-Host 'Guard task     (only visible to an elevated shell)' }

    Write-Host '--- local API ---'
    $h = Get-Health
    if ($h) {
        Write-Host ("latest reading : {0} (age {1}s, stale={2})" -f $h.latest_ts_eastern, $h.data_age_seconds, $h.stale)
        Write-Host ("rows / days    : {0} / {1}   db {2} MB" -f $h.rows, $h.files, $h.db_size_mb)
        if ($h.sync.last_error) { Write-Host ("last sync error: {0} at {1}" -f $h.sync.last_error, $h.sync.last_error_at) }
        Write-Host ("tunnel         : ready_connections={0} problems={1}" -f $h.tunnel.ready_connections, ($h.tunnel.problems -join ','))
    } else {
        Write-Host 'API is not answering on http://127.0.0.1:8001/health'
    }

    Write-Host '--- public URL ---'
    $url = Get-QuickTunnelUrl
    if (-not $url -and (Test-Path $UrlFile)) { $url = (Get-Content $UrlFile -TotalCount 1).Trim() }
    if ($url) { Write-Host $url } else { Write-Host 'unknown (tunnel not running?)' }
    # The API probes the public URL every minute through Cloudflare's edge,
    # bypassing local DNS caches that can hold a stale "no such host" for
    # 15 minutes after a new tunnel starts.
    if ($h -and $h.tunnel.last_public_probe.at) {
        $p = $h.tunnel.last_public_probe
        if ($p.http_status -eq 200) {
            Write-Host ("public /health OK at {0} ({1} ms)" -f $p.at, $p.elapsed_ms)
        } else {
            Write-Host ("public /health FAILED at {0}: {1} {2}" -f $p.at, $p.http_status, $p.error)
        }
    } elseif ($h) {
        Write-Host 'public /health not probed yet (first probe ~2 min after the tunnel starts)'
    }
}

function Invoke-Elevated {
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" $Command"
    Write-Host "Requesting administrator rights for '$Command' (approve the UAC prompt) ..."
    $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs -Wait -PassThru
    if (Test-Path $DeployLog) {
        Write-Host '--- deploy.log (tail) ---'
        Get-Content $DeployLog -Tail 40 | Out-Host
    }
    exit $p.ExitCode
}

if ($AdminCommands -contains $Command -and -not (Test-Admin)) {
    Invoke-Elevated
}

if ($AdminCommands -contains $Command) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    Start-Transcript -Path $DeployLog -Append | Out-Null
}

try {
    switch ($Command) {
        'install' { Install-Cassini; Show-Status }
        'uninstall' { Uninstall-Cassini }
        'start' {
            Start-Service $ApiSvc, $TunnelSvc
            Enable-ScheduledTask -TaskName $GuardTask | Out-Null
            Show-Status
        }
        'stop' {
            Disable-ScheduledTask -TaskName $GuardTask -ErrorAction SilentlyContinue | Out-Null
            Stop-Service $ApiSvc, $TunnelSvc -Force
            Write-Host "Stopped. The guard task is disabled until 'cassini.bat start'."
        }
        'restart-api' { Restart-Service $ApiSvc -Force; [void](Wait-ApiHealthy 120); Show-Status }
        'restart-tunnel' {
            Restart-Service $TunnelSvc -Force
            $url = Wait-TunnelUrl 120
            if ($url) { Write-Host "NEW PUBLIC API URL: $url  (update api_base in Streamlit secrets)" }
        }
        'status' { Show-Status }
        'url' {
            $url = Get-QuickTunnelUrl
            if (-not $url -and (Test-Path $UrlFile)) { $url = (Get-Content $UrlFile -TotalCount 1).Trim() }
            if ($url) { Write-Host $url; Set-Clipboard -Value $url; Write-Host '(copied to clipboard)' } else { Write-Host 'No public URL yet.' }
        }
    }
} catch {
    Write-Error $_
    exit 1
} finally {
    if ($AdminCommands -contains $Command) { Stop-Transcript | Out-Null }
}
