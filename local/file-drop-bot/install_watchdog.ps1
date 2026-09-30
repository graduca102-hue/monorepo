# Registers "FileDropBotWatchdog": every 2 minutes, revives file-drop-bot if it died.
$ErrorActionPreference = "Stop"
$dir = "C:\Users\ewg\Desktop\kiro-bot\project\file-drop-bot"
$pwshExe = (Get-Command pwsh).Source
$wd = Join-Path $dir "watchdog.ps1"
$taskName = "FileDropBotWatchdog"

$action = New-ScheduledTaskAction -Execute $pwshExe `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$wd`"" `
    -WorkingDirectory $dir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 2)).Repetition

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
"Watchdog '$taskName' registered (every 2 min)."
Start-ScheduledTask -TaskName $taskName
