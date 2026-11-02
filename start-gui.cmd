@echo off
cd /d "%~dp0"
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe" (
    start "" "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe" "%~dp0gui.py"
) else (
    py -3 "%~dp0gui.py"
    if errorlevel 1 pause
)
