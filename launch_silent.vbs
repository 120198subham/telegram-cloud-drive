' SS Workspace - Silent Launcher
' Called by Windows Task Scheduler at every login.
' Runs run_server.bat completely invisibly (no CMD window).

Dim WshShell
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """C:\Users\12019\.gemini\antigravity\scratch\telegram-cloud-drive\run_server.bat""", 0, False
Set WshShell = Nothing
