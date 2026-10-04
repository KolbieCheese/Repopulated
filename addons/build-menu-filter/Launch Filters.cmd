@echo off
if "%~1"=="" if exist "%~dp0Reassembly Filters.exe" (
  start "" "%~dp0Reassembly Filters.exe"
  exit /b 0
)
if "%~1"=="" if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0launcher.py"
  exit /b 0
)
set "FILTER_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%FILTER_PYTHON%" set "FILTER_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%FILTER_PYTHON%" (
  echo Run Setup.cmd first to install the standalone runtime.
  pause
  exit /b 1
)
"%FILTER_PYTHON%" "%~dp0launch.py" %*
if errorlevel 1 pause
