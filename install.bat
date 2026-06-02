@echo off
chcp 65001 >nul
title JARVIS v1.1 Installer
color 0B
echo.

:: 1. Das animierte Riesen-Logo laden (call verhindert, dass die .bat hier abbricht!)
call npx oh-my-logo@latest "JARVIS" ocean --filled

:: 2. Dein personalisierter Subtext in Blau
powershell -Command "Write-Host 'made by aquaxs-ai' -ForegroundColor Blue"

echo.
:: ==========================================================
:: AB HIER KANNST DU DEINEN NORMALEN INSTALLER-CODE WEITERLAUFEN LASSEN
:: ==========================================================

:: Python check
python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if errorlevel 1 (
        echo  [FEHLER] Python nicht gefunden!
        echo  Bitte Python 3.10+ von python.org installieren.
        echo  Wichtig: Haken bei "Add Python to PATH" setzen!
        pause & exit /b 1
    )
    set PYCMD=py
) else (
    set PYCMD=python
)

echo  [1/6] Python gefunden

echo  [2/6] pip sicherstellen...
%PYCMD% -m ensurepip --upgrade >nul 2>&1
%PYCMD% -m pip install --upgrade pip --quiet >nul 2>&1

echo  [3/6] Basis-Pakete installieren...
%PYCMD% -m pip install anthropic SpeechRecognition pyttsx3 pyautogui Pillow keyboard websockets requests numpy sounddevice openai google-generativeai --quiet
if errorlevel 1 (
    echo  [WARNUNG] Einige Pakete konnten nicht installiert werden.
    echo            Versuche einzeln...
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
echo  [3/6] Basis-Pakete fertig.

echo  [4/6] PyAudio installieren...
%PYCMD% -m pip install pyaudio --quiet >nul 2>&1
if errorlevel 1 (
    echo       Direktinstallation fehlgeschlagen, versuche pipwin...
    %PYCMD% -m pip install pipwin --quiet >nul 2>&1
    %PYCMD% -m pipwin install pyaudio >nul 2>&1
    if errorlevel 1 (
        echo       PyAudio nicht installierbar.
        echo       JARVIS nutzt sounddevice als Fallback - kein Problem!
    ) else (
        echo       PyAudio via pipwin OK.
    )
) else (
    echo       PyAudio direkt installiert.
)

echo  [5/6] Verzeichnisse erstellen...
if not exist "%USERPROFILE%\.jarvis\memory" mkdir "%USERPROFILE%\.jarvis\memory"
type nul > core\__init__.py 2>nul
type nul > memory\__init__.py 2>nul
type nul > agents\__init__.py 2>nul

echo  [6/6] Pruefe Installation...
%PYCMD% -c "import anthropic; print('  anthropic OK')"
%PYCMD% -c "import keyboard; print('  keyboard OK')"
%PYCMD% -c "import websockets; print('  websockets OK')"
%PYCMD% -c "import pyautogui; print('  pyautogui OK')"

call npx oh-my-logo@latest "INSTALLATION\nCOMPLETE" forest --filled
powershell -Command "Write-Host 'you can start with python jarvis.py' -ForegroundColor Green"

echo.
pause