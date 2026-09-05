@echo off
rem Sets up the dashboard on THIS Windows device using the files next to this
rem script: builds it, puts a "Dashboard" shortcut on the desktop, and schedules
rem a rebuild every 6 hours. Safe to run again; it replaces what it made before.
setlocal
cd /d "%~dp0"
where py >nul 2>&1 && (py -3 "%~dp0install.py" %*) || (python "%~dp0install.py" %*)
echo.
pause
