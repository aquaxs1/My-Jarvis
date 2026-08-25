@echo off
chcp 65001 >nul
title My Jarvis v1.1 Installer
color 0B
echo.

:: 1. Load the animated giant logo (call keeps the .bat from stopping here!)
call npx oh-my-logo@latest "JARVIS" ocean --filled

:: 2. The personalised subtitle, in blue
powershell -Command "Write-Host 'made by aquaxs-ai' -ForegroundColor Blue"

echo.
:: ==========================================================
:: THE NORMAL INSTALLER CODE CARRIES ON FROM HERE
:: ==========================================================

:: Python check
python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Python not found!
        echo  Please install Python 3.10+ from python.org.
        echo  Important: tick "Add Python to PATH"!
        pause & exit /b 1
    )
    set PYCMD=py
) else (
    set PYCMD=python
)

echo  [1/6] Python found

echo  [2/6] Making sure pip is there...
%PYCMD% -m ensurepip --upgrade >nul 2>&1
%PYCMD% -m pip install --upgrade pip --quiet >nul 2>&1

echo  [3/6] Installing the base packages...
%PYCMD% -m pip install anthropic SpeechRecognition pyttsx3 pyautogui Pillow keyboard websockets requests numpy sounddevice openai google-generativeai --quiet
if errorlevel 1 (
    echo  [WARNING] Some packages could not be installed.
    echo            Trying them one by one...
    %PYCMD% -m pip install anthropic --quiet
    %PYCMD% -m pip install SpeechRecognition --quiet
    %PYCMD% -m pip install pyttsx3 --quiet
    %PYCMD% -m pip install pyautogui --quiet
    %PYCMD% -m pip install Pillow --quiet
    %PYCMD% -m pip install keyboard --quiet
    %PYCMD% -m pip install websockets --quiet
    %PYCMD% -m pip install requests numpy sounddevice --quiet
    %PYCMD% -m pip install openai google-generativeai --quiet
)
echo  [3/6] Base packages done.

echo  [4/6] Installing PyAudio...
%PYCMD% -m pip install pyaudio --quiet >nul 2>&1
if errorlevel 1 (
    echo       Direct install failed, trying pipwin...
    %PYCMD% -m pip install pipwin --quiet >nul 2>&1
    %PYCMD% -m pipwin install pyaudio >nul 2>&1
    if errorlevel 1 (
        echo       PyAudio cannot be installed.
        echo       My Jarvis falls back to sounddevice - no problem!
    ) else (
        echo       PyAudio via pipwin OK.
    )
) else (
    echo       PyAudio installed directly.
)

echo  [5/6] Creating directories...
if not exist "%USERPROFILE%\.jarvis\memory" mkdir "%USERPROFILE%\.jarvis\memory"
type nul > core\__init__.py 2>nul
type nul > memory\__init__.py 2>nul
type nul > agents\__init__.py 2>nul

echo  [6/6] Checking the installation...
%PYCMD% -c "import anthropic; print('  anthropic OK')"
%PYCMD% -c "import keyboard; print('  keyboard OK')"
%PYCMD% -c "import websockets; print('  websockets OK')"
%PYCMD% -c "import pyautogui; print('  pyautogui OK')"

call npx oh-my-logo@latest "INSTALLATION\nCOMPLETE" forest --filled
powershell -Command "Write-Host 'you can start with python jarvis.py' -ForegroundColor Green"

echo.
pause