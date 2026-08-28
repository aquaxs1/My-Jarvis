@echo off
:: Starts My Jarvis and, unlike a double-clicked jarvis.py, keeps the window
:: open when something goes wrong -- an error you cannot read is the same as no
:: error at all.
chcp 65001 >nul
title My Jarvis
cd /d "%~dp0"

:: jarvis.py holds the console itself when it is started on its own; here the
:: launcher does it, so a failure is not two Enter presses.
set JARVIS_LAUNCHER=1

python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  [ERROR] Python not found.
        echo  Install Python 3.10+ from python.org and tick "Add Python to PATH".
        echo.
        pause & exit /b 1
    )
    set PYCMD=py
) else (
    set PYCMD=python
)

echo  Starting My Jarvis... the interface opens in your browser.
echo  Leave this window open while you use it.
echo.
%PYCMD% jarvis.py %*
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
    echo.
    echo  My Jarvis stopped with error code %EXITCODE%. The cause is above.
    echo.
    pause
)
exit /b %EXITCODE%
