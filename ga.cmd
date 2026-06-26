@echo off
cd /d "%~dp0"
set "PY=python"
if not exist "%PY%" set "PY=python"
echo ╔══════════════════════════════════════╗
echo ║   GenericAgent - Launching GUI...   ║
echo ╚══════════════════════════════════════╝
"%PY%" "%~dp0launch.pyw" %*
