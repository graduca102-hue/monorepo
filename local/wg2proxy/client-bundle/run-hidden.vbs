' Launch wireproxy silently in the background (no console window).
' Double-click this file to start the proxy.
' To stop: open Task Manager → find wireproxy.exe → End task.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "wireproxy.exe -c wireproxy.conf", 0, False
