@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 (
    echo Installation failed. See the message above and README.md.
    pause
    exit /b 1
)
echo Ready. Double-click "Start Atelier.vbs" to launch Atelier.
pause
