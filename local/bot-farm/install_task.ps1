# Registers "BotFarm" scheduled task: runs the bot-manager at user logon,
# hidden, detached from any IDE, and auto-restarts on failure.
$ErrorActionPreference = "Stop"

$dir      = "C:\Users\ewg\Desktop\kiro-bot\project\bot-farm"
$pythonw  = "C:\Users\ewg\AppData\Local\Programs\Python\Python311\pythonw.exe"
$script   = Join-Path $dir "run.py"
$taskName = "BotFarm"

if (-not (Test-Path $pythonw)) { throw "pythonw not found: $pythonw" }
if (-not (Test-Path $script))  { throw "run.py not found: $script" }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

"Task '$taskName' registered. Starting it now..."
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
