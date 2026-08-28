# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the My Jarvis desktop build.

Build with:

    pyinstaller packaging/jarvis.spec --noconfirm --clean

Produces a single-file executable that starts My Jarvis exactly the way
`python jarvis.py` does: it brings up the local server and opens the
interface at 127.0.0.1:8765. Run from the repo root so the paths below
resolve.

Two things about this project need spelling out to PyInstaller.

1. The interface is a folder of files (gui/index.html plus assets and fonts)
   that core/gui_server.py reads at runtime via
   `Path(__file__).parent.parent / "gui"`. Under PyInstaller that resolves to
   <bundle>/gui, so the folder is bundled at exactly that path.

2. Roughly twenty integrations are imported *inside* functions, guarded by
   `except ImportError`, so that My Jarvis starts even when they are not set
   up. PyInstaller only follows module-level imports, so it would drop every
   one of them and the packaged build would quietly lose features that a
   `pip install` build has. They are therefore listed as hidden imports
   below. Anything genuinely absent at build time is skipped by the
   `excludes` logic in the workflow rather than failing the build.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parent
ICONS = REPO_ROOT / "packaging" / "icons"

datas = [
    # Must land at <bundle>/gui — see note 1 above.
    (str(REPO_ROOT / "gui"), "gui"),
    (str(REPO_ROOT / "assets"), "assets"),
]

# See note 2: everything reached only through a function-local import.
hiddenimports = [
    # First-party
    "core",
    "agents",
    # AI providers
    "anthropic",
    # core/brain.py takes whichever of these the installed anthropic brought in.
    "httpx",
    "httpx2",
    "openai",
    "google.generativeai",
    # Voice
    "speech_recognition",
    "pyttsx3",
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
    "pyttsx3.drivers.nsss",
    "pyttsx3.drivers.espeak",
    "sounddevice",
    "pvporcupine",
    # System control
    "pyautogui",
    "keyboard",
    "psutil",
    # Calendar and tasks
    "google.oauth2.credentials",
    "google.auth.transport.requests",
    "google_auth_oauthlib.flow",
    "googleapiclient.discovery",
    "todoist_api_python",
    "notion_client",
    "dateparser",
    # Documents and research
    "pdfplumber",
    "pypdf",
    "docx",
    "feedparser",
    "youtube_transcript_api",
    # Smart home
    "phue",
    # Storage and crypto
    "keyring",
    "keyring.backends",
    "cryptography",
]

icon = None
if sys.platform == "win32":
    candidate = ICONS / "jarvis.ico"
    icon = str(candidate) if candidate.is_file() else None
elif sys.platform == "darwin":
    candidate = ICONS / "jarvis.icns"
    icon = str(candidate) if candidate.is_file() else None

a = Analysis(
    [str(REPO_ROOT / "jarvis.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "black",  # a dev-time formatter; the runtime code path is optional
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries trip antivirus heuristics.
    upx_exclude=[],
    runtime_tmpdir=None,
    # Console stays on: My Jarvis prints its startup state, the port it
    # settled on and any configuration errors. The interface itself opens in
    # the browser.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
