@echo off
rem Double-click this (from \\wsl.localhost\...\webapp\scripts\windows) to put
rem an "SMCP Kallakudi" icon on the Windows desktop.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-desktop-icon.ps1"
echo.
pause
