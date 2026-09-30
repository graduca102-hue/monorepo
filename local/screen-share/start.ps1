# Start a live screen-share (MJPEG) and expose it on srv2's public IP.
#   phone/browser -> http://<srv2>:<PubPort>/<token>  (live desktop, ~12 fps)
# Sends the private URL to OWNER_ID via the bot token in .env.
#
# Usage:  pwsh -NoProfile -File project\screen-share\start.ps1 [-Minutes 30]
param(
    [int]$Minutes = 30,
    [int]$Port = 8790,      # local MJPEG server (loopback only)
    [int]$PubPort = 8792,   # public port on srv2
    [int]$FwdPort = 18790,  # srv2 loopback port for the ssh reverse tunnel
    [int]$Fps = 12,
    [int]$Width = 1280,
    [string]$Srv2Ip = '31.77.145.160'
)
$ErrorActionPreference = 'Stop'
$root = 'C:\Users\ewg\Desktop\kiro-bot'
$here = Join-Path $root 'project\screen-share'
$py = Join-Path $root '.venv\Scripts\python.exe'
$ffmpeg = 'D:\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe'

$token = -join ((1..24) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
$runlog = Join-Path $here 'run'
New-Item -ItemType Directory -Force -Path $runlog | Out-Null

# 1) local MJPEG server (127.0.0.1 only)
$env:SCREEN_TOKEN = $token
$env:SCREEN_PORT = "$Port"
$env:SCREEN_FPS = "$Fps"
$env:SCREEN_WIDTH = "$Width"
$env:FFMPEG_PATH = $ffmpeg
$srv = Start-Process -FilePath $py -ArgumentList (Join-Path $here 'serve.py') `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runlog 'serve.out.log') `
    -RedirectStandardError  (Join-Path $runlog 'serve.err.log')
Start-Sleep -Seconds 2

# 2) ssh reverse tunnel + public relay on srv2
$tunLog = Join-Path $runlog 'tunnel.log'
Remove-Item $tunLog, "$tunLog.err" -ErrorAction SilentlyContinue
$tun = Start-Process -FilePath $py `
    -ArgumentList @((Join-Path $here 'tunnel.py'), "$Port", "$PubPort", "$FwdPort") `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $tunLog -RedirectStandardError "$tunLog.err"

$ready = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Seconds 1
    if ((Select-String -Path $tunLog -Pattern 'tunnel up' -ErrorAction SilentlyContinue)) { $ready = $true; break }
    if ($tun.HasExited) { break }
}
if (-not $ready) {
    Stop-Process -Id $srv.Id, $tun.Id -Force -ErrorAction SilentlyContinue
    Write-Output "--- tunnel.log ---"; Get-Content $tunLog, "$tunLog.err" -ErrorAction SilentlyContinue
    throw 'tunnel did not come up'
}

$fullUrl = "http://${Srv2Ip}:${PubPort}/$token"

# 3) detached auto-stop timer
$stop = Join-Path $here 'stop.ps1'
$when = (Get-Date).AddMinutes($Minutes)
$timer = Start-Process -FilePath 'pwsh' -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-Command', "Start-Sleep -Seconds $($Minutes * 60); & '$stop'"
)

@{
    started   = (Get-Date -Format o)
    minutes   = $Minutes
    serve_pid = $srv.Id
    tunnel_pid = $tun.Id
    timer_pid = $timer.Id
    pub_port  = $PubPort
    url       = $fullUrl
} | ConvertTo-Json | Set-Content (Join-Path $runlog 'state.json')

# 4) notify owner
$lines = Get-Content (Join-Path $root '.env')
$bt = (($lines | Where-Object { $_ -match '^BOT_TOKEN=' }) -replace '^BOT_TOKEN=', '').Trim()
$owner = (($lines | Where-Object { $_ -match '^OWNER_ID=' }) -replace '^OWNER_ID=', '').Trim()
$msg = "Демонстрация экрана запущена (~$Fps fps).`nОткрой ссылку в браузере (телефон/пк):`n$fullUrl`n`nСсылка приватная (случайный токен), но идёт по HTTP без шифрования. Авто-стоп через $Minutes мин. Останов вручную: stop.ps1"
try {
    $r = Invoke-RestMethod -Uri "https://api.telegram.org/bot$bt/sendMessage" -Method Post -Form @{
        chat_id = $owner; text = $msg; disable_web_page_preview = 'true'
    }
    Write-Output "notified ok=$($r.ok)"
}
catch { Write-Output "notify failed: $($_.Exception.Message)" }

Write-Output "url: $fullUrl"
Write-Output "serve_pid=$($srv.Id) tunnel_pid=$($tun.Id)  auto-stop $($when.ToString('HH:mm'))"
