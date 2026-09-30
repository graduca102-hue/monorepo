# Stop the live screen-share: kill local server + tunnel + timer, and the relay on srv2.
$ErrorActionPreference = 'SilentlyContinue'
$root = 'C:\Users\ewg\Desktop\kiro-bot'
$here = Join-Path $root 'project\screen-share'
$state = Join-Path $here 'run\state.json'

if (Test-Path $state) {
    $s = Get-Content $state -Raw | ConvertFrom-Json
    foreach ($p in @($s.serve_pid, $s.tunnel_pid, $s.timer_pid)) {
        if ($p) { Stop-Process -Id $p -Force }
    }
}

# stray local processes
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'screen-share\\(serve|tunnel)\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# kill the public relay on srv2
$remote = 'if [ -f /tmp/kiro_screen_relay.pid ]; then kill "$(cat /tmp/kiro_screen_relay.pid)" 2>/dev/null; fi; rm -f /tmp/kiro_screen_relay.py /tmp/kiro_screen_relay.log /tmp/kiro_screen_relay.pid'
try {
    & (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'srv_ssh.py') srv2 $remote | Out-Null
}
catch {}

Remove-Item $state -ErrorAction SilentlyContinue
Write-Output 'screen-share stopped'
