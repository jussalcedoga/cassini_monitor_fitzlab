@echo off
REM Cassini BlueFors monitor service manager. Run "cassini.bat" for status,
REM or: install | uninstall | start | stop | restart-api | restart-tunnel | url
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\cassini.ps1" %*
