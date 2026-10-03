@echo off
rem NexGen Transport -- start everything on http://127.0.0.1:8100
rem
rem   run.bat            create .venv and build the web app if missing, then start
rem   run.bat /build     rebuild the web app first (after a change under web\)
rem   run.bat /setup     reinstall Python and web dependencies first
rem
rem Stop with stop.bat (or Ctrl+C in this window).
setlocal
cd /d "%~dp0"

set SETUP=0
set BUILD=0
for %%A in (%*) do (
  if /I "%%A"=="/setup" set SETUP=1
  if /I "%%A"=="/build" set BUILD=1
)

if not exist ".venv\Scripts\python.exe" set SETUP=1
if "%SETUP%"=="1" (
  echo Creating the Python environment...
  if not exist ".venv\Scripts\python.exe" python -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || goto :fail
)

if not exist ".env" (
  echo No .env found. Create one with DB_USER and DB_PASSWORD -- see docs\CONFIGURATION.md
  goto :fail
)

if exist "web\package.json" (
  if not exist "web\node_modules" (
    echo Installing web dependencies...
    call npm --prefix web install || goto :fail
  )
  if "%SETUP%"=="1" set BUILD=1
  if not exist "web\dist\index.html" set BUILD=1
  if "%BUILD%"=="1" (
    echo Building the web app...
    call npm --prefix web run build || goto :fail
  )
)

echo Starting NexGen Transport on http://127.0.0.1:8100
".venv\Scripts\python.exe" -m nexgen run
goto :eof

:fail
echo.
echo Start-up stopped. See the message above.
exit /b 1
