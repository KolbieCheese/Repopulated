@echo off
setlocal
where py >nul 2>nul
if errorlevel 1 (
  echo Install 64-bit Python 3.11 or newer from python.org with the Python launcher enabled, then run this again.
  pause
  exit /b 1
)
py -3 -m venv "%~dp0.venv"
if errorlevel 1 goto failure
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failure
echo Ready. Double-click Launch Filters.cmd to play.
pause
exit /b 0
:failure
echo Setup failed. Check the messages above.
pause
exit /b 1
