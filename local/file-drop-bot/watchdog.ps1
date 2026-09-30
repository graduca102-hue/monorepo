# Ensures file-drop-bot is alive. If not, (re)starts the FileDropBot task.
# Meant to be run every couple of minutes by the FileDropBotWatchdog task.
$running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' or Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*file-drop-bot*run.py*" }
if (-not $running) {
    try { Enable-ScheduledTask -TaskName FileDropBot -ErrorAction SilentlyContinue | Out-Null } catch {}
    try { Start-ScheduledTask -TaskName FileDropBot -ErrorAction SilentlyContinue } catch {}
    "$(Get-Date -Format o) watchdog: file-drop-bot was down -> started" |
        Out-File -Append -Encoding utf8 "C:\Users\ewg\Desktop\kiro-bot\project\file-drop-bot\watchdog.log"
}
