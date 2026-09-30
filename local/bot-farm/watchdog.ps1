# Ensures bot-farm is alive. If not, (re)starts the BotFarm task.
# Meant to be run every couple of minutes by the BotFarmWatchdog task.
$running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' or Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*app.main*" }
if (-not $running) {
    try { Enable-ScheduledTask -TaskName BotFarm -ErrorAction SilentlyContinue | Out-Null } catch {}
    try { Start-ScheduledTask -TaskName BotFarm -ErrorAction SilentlyContinue } catch {}
    "$(Get-Date -Format o) watchdog: bot-farm was down -> started" |
        Out-File -Append -Encoding utf8 "C:\Users\ewg\Desktop\kiro-bot\project\bot-farm\watchdog.log"
}
