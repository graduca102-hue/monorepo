$ErrorActionPreference = "SilentlyContinue"
$logDir  = "C:\Sandbox\Logs"
$ts      = Get-Date -Format "yyyyMMdd_HHmmss"
$procLog = Join-Path $logDir "processes_$ts.log"
$fsLog   = Join-Path $logDir "filesystem_$ts.log"
$regLog  = Join-Path $logDir "registry_$ts.log"
$netLog  = Join-Path $logDir "network_$ts.log"
$dnsLog  = Join-Path $logDir "dns_$ts.log"

function Log($path, $msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss.fff"), $msg
    Add-Content -Path $path -Value $line
}

Log $procLog "=== observer online ==="

Register-CimIndicationEvent -Query "SELECT * FROM Win32_ProcessStartTrace" -SourceIdentifier "ProcStart" -Action {
    $e = $Event.SourceEventArgs.NewEvent
    $ppid = $e.ParentProcessID
    $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$ppid").Name
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($e.ProcessID)").CommandLine
    $line = "{0}  START pid={1,-6} ppid={2,-6} parent={3,-25} name={4,-25} cmd={5}" -f (Get-Date -Format "HH:mm:ss.fff"), $e.ProcessID, $ppid, $parent, $e.ProcessName, $cmd
    Add-Content -Path "C:\Sandbox\Logs\processes_$($using:ts).log" -Value $line
} | Out-Null

Register-CimIndicationEvent -Query "SELECT * FROM Win32_ProcessStopTrace" -SourceIdentifier "ProcStop" -Action {
    $e = $Event.SourceEventArgs.NewEvent
    $line = "{0}  STOP  pid={1,-6} name={2,-25} exit={3}" -f (Get-Date -Format "HH:mm:ss.fff"), $e.ProcessID, $e.ProcessName, $e.ExitStatus
    Add-Content -Path "C:\Sandbox\Logs\processes_$($using:ts).log" -Value $line
} | Out-Null

$watchDirs = @(
    "C:\Users\WDAGUtilityAccount\AppData",
    "C:\Users\WDAGUtilityAccount\Desktop",
    "C:\Users\WDAGUtilityAccount\Documents",
    "C:\Users\Public",
    "C:\ProgramData",
    "C:\Windows\Temp",
    "C:\Windows\System32\Tasks"
)

$watchers = @()
foreach ($d in $watchDirs) {
    if (-not (Test-Path $d)) { continue }
    $w = New-Object System.IO.FileSystemWatcher $d, "*"
    $w.IncludeSubdirectories = $true
    $w.EnableRaisingEvents   = $true
    $w.NotifyFilter = [System.IO.NotifyFilters]::FileName -bor [System.IO.NotifyFilters]::LastWrite -bor [System.IO.NotifyFilters]::CreationTime

    Register-ObjectEvent $w Created -SourceIdentifier "FS_C_$([Guid]::NewGuid())" -Action {
        $line = "{0}  CREATE  {1}" -f (Get-Date -Format "HH:mm:ss.fff"), $Event.SourceEventArgs.FullPath
        Add-Content -Path "C:\Sandbox\Logs\filesystem_$($using:ts).log" -Value $line
    } | Out-Null
    Register-ObjectEvent $w Changed -SourceIdentifier "FS_M_$([Guid]::NewGuid())" -Action {
        $line = "{0}  MODIFY  {1}" -f (Get-Date -Format "HH:mm:ss.fff"), $Event.SourceEventArgs.FullPath
        Add-Content -Path "C:\Sandbox\Logs\filesystem_$($using:ts).log" -Value $line
    } | Out-Null
    Register-ObjectEvent $w Deleted -SourceIdentifier "FS_D_$([Guid]::NewGuid())" -Action {
        $line = "{0}  DELETE  {1}" -f (Get-Date -Format "HH:mm:ss.fff"), $Event.SourceEventArgs.FullPath
        Add-Content -Path "C:\Sandbox\Logs\filesystem_$($using:ts).log" -Value $line
    } | Out-Null
    Register-ObjectEvent $w Renamed -SourceIdentifier "FS_R_$([Guid]::NewGuid())" -Action {
        $line = "{0}  RENAME  {1} -> {2}" -f (Get-Date -Format "HH:mm:ss.fff"), $Event.SourceEventArgs.OldFullPath, $Event.SourceEventArgs.FullPath
        Add-Content -Path "C:\Sandbox\Logs\filesystem_$($using:ts).log" -Value $line
    } | Out-Null

    $watchers += $w
}

$regKeys = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
    "HKLM:\SYSTEM\CurrentControlSet\Services",
    "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
    "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
)

$regSnapshot = @{}
foreach ($k in $regKeys) {
    if (Test-Path $k) {
        $regSnapshot[$k] = (Get-ItemProperty $k -ErrorAction SilentlyContinue | Out-String)
    }
}

$regJob = Start-Job -ArgumentList $regKeys, $regLog, $regSnapshot -ScriptBlock {
    param($keys, $log, $snap)
    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($k in $keys) {
            if (-not (Test-Path $k)) { continue }
            $cur = (Get-ItemProperty $k -ErrorAction SilentlyContinue | Out-String)
            if ($snap[$k] -ne $cur) {
                $line = "{0}  CHANGE {1}`n{2}" -f (Get-Date -Format "HH:mm:ss.fff"), $k, $cur
                Add-Content -Path $log -Value $line
                $snap[$k] = $cur
            }
        }
    }
}

$netJob = Start-Job -ArgumentList $netLog -ScriptBlock {
    param($log)
    $seen = @{}
    while ($true) {
        Start-Sleep -Milliseconds 500
        $conns = Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Established' -or $_.State -eq 'SynSent' }
        foreach ($c in $conns) {
            $key = "$($c.OwningProcess)-$($c.RemoteAddress):$($c.RemotePort)"
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $procName = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
                $line = "{0}  TCP lport={1,-6} pid={2,-6} proc={3,-20} -> {4}:{5}  state={6}" -f (Get-Date -Format "HH:mm:ss.fff"), $c.LocalPort, $c.OwningProcess, $procName, $c.RemoteAddress, $c.RemotePort, $c.State
                Add-Content -Path $log -Value $line
            }
        }
    }
}

$dnsJob = Start-Job -ArgumentList $dnsLog -ScriptBlock {
    param($log)
    $seen = @{}
    while ($true) {
        Start-Sleep -Seconds 1
        $cache = Get-DnsClientCache -ErrorAction SilentlyContinue
        foreach ($e in $cache) {
            $key = "$($e.Entry)-$($e.Data)"
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $line = "{0}  DNS {1,-40} -> {2}" -f (Get-Date -Format "HH:mm:ss.fff"), $e.Entry, $e.Data
                Add-Content -Path $log -Value $line
            }
        }
    }
}

Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = "Sandbox Observer -- RUNNING"
$form.Size = New-Object System.Drawing.Size(620,200)
$form.StartPosition = "CenterScreen"
$form.TopMost = $true

$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = "Observer active.`nLogs -> C:\Sandbox\Logs\ (mapped to host).`nSample  -> C:\Sandbox\Shared\`n`nClose this window to stop observer."
$lbl.Dock = 'Fill'
$lbl.Font = New-Object System.Drawing.Font("Consolas", 10)
$form.Controls.Add($lbl)

$form.Add_FormClosing({
    Get-EventSubscriber | Unregister-Event
    Stop-Job $regJob, $netJob, $dnsJob -ErrorAction SilentlyContinue
    Remove-Job $regJob, $netJob, $dnsJob -Force -ErrorAction SilentlyContinue
    Log $procLog "=== observer offline ==="
})

[System.Windows.Forms.Application]::Run($form)
