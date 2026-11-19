@echo off
rem Opens Telegram and MT5 if they are closed, then starts the bots. The supervisor refuses a second copy.
rem A portable MT5 in %USERPROFILE%\MT5 is used when present; otherwise the installed one.
set WAIT=0

set TELEGRAM=
for %%T in ("%APPDATA%\Telegram Desktop\Telegram.exe" "%LOCALAPPDATA%\Telegram Desktop\Telegram.exe") do (
    if not defined TELEGRAM if exist %%T set TELEGRAM=%%~T
)

set MT5ARGS=
if exist "%USERPROFILE%\MT5\terminal64.exe" (
    set "MT5=%USERPROFILE%\MT5\terminal64.exe"
    set "TRADER_MT5=%USERPROFILE%\MT5\terminal64.exe"
    set MT5ARGS=/portable
) else (
    set "MT5=C:\Program Files\MetaTrader 5\terminal64.exe"
)

set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYTHONW%" set PYTHONW=pythonw

if defined TELEGRAM (
    tasklist /FI "IMAGENAME eq Telegram.exe" /NH | find /I "Telegram.exe" >nul
    if errorlevel 1 start "" "%TELEGRAM%"
)

tasklist /FI "IMAGENAME eq terminal64.exe" /NH | find /I "terminal64.exe" >nul
if errorlevel 1 (
    start "" "%MT5%" %MT5ARGS%
    set WAIT=20
)

if not "%WAIT%"=="0" timeout /t %WAIT% /nobreak >nul
start "" "%PYTHONW%" "%~dp0supervisor.py"
