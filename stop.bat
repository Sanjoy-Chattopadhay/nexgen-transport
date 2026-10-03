@echo off
rem NexGen Transport -- stop every service and the gateway.
rem Only NexGen's own processes are touched; the legacy Smart-Truck and
rem Geo-Fencing apps (ports 8000, 8001, 8090) keep running.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m nexgen shutdown
