# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the My Jarvis desktop build.

Build with:

    pyinstaller packaging/jarvis.spec --noconfirm --clean

Produces a folder build (dist/jarvis/) that starts My Jarvis exactly the
way `python jarvis.py` does: it brings up the local server and opens the
interface at 127.0.0.1:8765. Run from the repo root so the paths below
resolve.

A folder, not a single file, on purpose. PyInstaller's onefile mode
produces a self-extracting executable that unpacks itself into %TEMP% and
runs from there -- which is what a dropper does, so Microsoft Defender's
machine-learning model flagged the v2.8.2 download as
Trojan:Win32/Sabsik.TE.A!ml. It was a false positive, but not a mystery:
onefile packing, no signature, no version resource and a program that
genuinely hooks the keyboard and captures the screen add up to something
that looks the part. The folder build removes the self-extraction, the
version resource and icon below remove the missing-metadata signals, and
what remains is the unsigned-binary warning that only a code-signing
certificate can fix. The release workflow zips dist/jarvis/ for download.

Three things about this project need spelling out to PyInstaller.

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

3. The Windows version resource and the icon are built from files in this
   directory: version_info.txt and icons/jarvis.ico (rendered from
   site/favicon.svg). Both are Windows-only and are skipped elsewhere.
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
version = None
if sys.platform == "win32":
    candidate = ICONS / "jarvis.ico"
    icon = str(candidate) if candidate.is_file() else None
    # A Windows-only resource: PyInstaller rejects it on other platforms.
    version_file = REPO_ROOT / "packaging" / "version_info.txt"
    version = str(version_file) if version_file.is_file() else None
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

# exclude_binaries=True keeps the payload out of the executable: COLLECT
# below places it beside jarvis.exe instead. That is the whole point of the
# folder build -- nothing unpacks itself at runtime.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries trip antivirus heuristics.
    upx_exclude=[],
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
    version=version,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="jarvis",
)
