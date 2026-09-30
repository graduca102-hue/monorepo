$ErrorActionPreference = "SilentlyContinue"

$defReg = "HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender"
New-Item -Path $defReg -Force | Out-Null
Set-ItemProperty -Path $defReg -Name "DisableAntiSpyware" -Value 1 -Type DWord
Set-ItemProperty -Path $defReg -Name "DisableAntiVirus"   -Value 1 -Type DWord
Set-ItemProperty -Path $defReg -Name "DisableRoutinelyTakingAction" -Value 1 -Type DWord

$rtp = "$defReg\Real-Time Protection"
New-Item -Path $rtp -Force | Out-Null
Set-ItemProperty -Path $rtp -Name "DisableRealtimeMonitoring" -Value 1 -Type DWord
Set-ItemProperty -Path $rtp -Name "DisableBehaviorMonitoring" -Value 1 -Type DWord
Set-ItemProperty -Path $rtp -Name "DisableOnAccessProtection" -Value 1 -Type DWord
Set-ItemProperty -Path $rtp -Name "DisableScanOnRealtimeEnable" -Value 1 -Type DWord
Set-ItemProperty -Path $rtp -Name "DisableIOAVProtection" -Value 1 -Type DWord

Set-MpPreference -DisableRealtimeMonitoring $true
Set-MpPreference -DisableBehaviorMonitoring $true
Set-MpPreference -DisableBlockAtFirstSeen $true
Set-MpPreference -DisableIOAVProtection $true
Set-MpPreference -DisableScriptScanning $true
Set-MpPreference -MAPSReporting Disabled
Set-MpPreference -SubmitSamplesConsent NeverSend
Set-MpPreference -EnableControlledFolderAccess Disabled

Stop-Service -Name WinDefend    -Force
Stop-Service -Name WdNisSvc     -Force
Stop-Service -Name SecurityHealthService -Force
Stop-Service -Name Sense        -Force

Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System" -Name "EnableSmartScreen" -Value 0 -Force -ErrorAction SilentlyContinue

Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name "EnableLUA" -Value 0 -Force

Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -WindowStyle Normal -File C:\Sandbox\Tools\observer.ps1"

Start-Process "C:\Sandbox\Shared"
