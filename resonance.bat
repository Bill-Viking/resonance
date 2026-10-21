@echo off
setlocal
cd /d "%~dp0" || exit /b 1
where py >nul 2>nul
if errorlevel 1 goto python
py -3 server.py --open %*
exit /b %errorlevel%
:python
python server.py --open %*
exit /b %errorlevel%
