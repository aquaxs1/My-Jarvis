"""
JARVIS Executor
Führt Aktionen auf dem PC aus: Maus, Tastatur, Programme
"""

import os
import re
import time
import shlex
import subprocess
import platform
import asyncio
import tempfile
from typing import Optional
from urllib.parse import quote_plus
from core.safety import SafetyGuard


# Whitelist of programs that are safe to open
ALLOWED_PROGRAMS = {
    # Browsers
    "chrome", "firefox", "msedge", "opera", "brave",
    "google-chrome", "chromium", "safari",
    # Editors / IDEs
    "notepad", "notepad++", "code", "vim", "nano", "gedit",
    "sublime_text", "atom", "pycharm", "idea",
    # Microsoft Office
    "winword", "excel", "powerpnt", "outlook", "onenote", "mspaint",
    # Media
    "vlc", "spotify", "wmplayer", "foobar2000",
    # Utilities
    "calc", "explorer", "taskmgr", "snippingtool", "mstsc",
    # Communication
    "slack", "discord", "teams", "telegram", "zoom",
    # System
    "control", "devmgmt.msc", "services.msc",
    # macOS specific
    "finder", "preview", "textedit", "activity monitor",
    # Linux specific
    "nautilus", "thunar", "dolphin", "xterm", "gnome-terminal",
    "konsole", "evince", "eog",
}

# Programs / commands that are explicitly blocked (dangerous operations)
BLOCKED_PROGRAMS = {
    "del", "rm", "rmdir", "format", "shutdown", "restart",
    "reg", "regedit", "diskpart", "cipher", "sfc",
    "bcdedit", "bootrec", "fdisk", "mkfs", "dd",
    "kill", "killall", "pkill", "taskkill",
    "net", "netsh", "icacls", "takeown",
    "wget", "curl",  # prevent arbitrary downloads
    "powershell.exe", "powershell", "cmd.exe", "cmd",
    "terminal", "wt",
}

# Pattern to detect shell injection attempts in program names.
# Bug 5 hardening: control chars, quotes, shell metacharacters, NUL byte, zero-width whitespace.
# Spaces ARE allowed (e.g. "activity monitor"); control chars are not.
_INJECTION_PATTERN = re.compile(
    "[;&|`$(){}\[\]!<>\"'\\\x00-\x1f\x7f\u00a0\u200b-\u200f\u2028\u2029]"
)
# Pattern to detect shell injection attempts in arbitrary commands (run_command).
# Catches command-separators, command-substitution, process-substitution, redirection,
# null bytes, line-continuations and CR/LF smuggling.
_CMD_INJECTION_PATTERN = re.compile(
    r"`"                       # backticks
    r"|\$\("                   # $(...) command substitution
    r"|\$\{"                   # ${...} variable expansion
    r"|<\("                    # <(...) process substitution
    r"|>\("                    # >(...) process substitution
    r"|;\s*\w"                 # ; followed by another command
    r"|&&|\|\|"                # logical chain
    r"|\|"                     # plain pipe
    r"|>>?|<<?"                # redirection
    r"|\\\n"                   # line continuation
    r"|[\x00\r\n]"             # NUL / CR / LF smuggling
)
_MAX_PROGRAM_NAME_LEN = 260   # MAX_PATH on Windows
_MAX_COMMAND_LEN = 2048

# v2.9: erlaubte Endungen für launch_app (Start eines gescannten Installations-Pfads).
# .lnk (Startmenü-Verknüpfung) und .exe sind startbar; alles andere wird abgelehnt.
_LAUNCH_EXTS = (".exe", ".lnk")
# Steuer-/Smuggling-Zeichen, die in keinem legitimen Pfad vorkommen.
_PATH_CTRL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def _validate_launch_path(path: str) -> tuple[bool, str]:
    """v2.9: Validiert einen vollständigen Pfad für launch_app.

    Anders als _open_program (Whitelist nach Name) akzeptiert dies konkrete
    Pfade, die der App-Index real auf diesem System gefunden hat. Sicher, weil:
    - die Datei muss tatsächlich existieren,
    - Endung muss .exe/.lnk sein (kein Skript, kein Batch),
    - keine Steuer-/CRLF/NUL-Zeichen (kein Smuggling),
    - Start via os.startfile/Popen ohne Shell (keine Metazeichen-Interpretation).
    Returns (is_valid, reason).
    """
    if not path or not path.strip():
        return False, "Empty path"
    path = path.strip().strip('"')
    if len(path) > _MAX_PROGRAM_NAME_LEN:
        return False, "Path too long"
    if _PATH_CTRL_PATTERN.search(path):
        return False, "Path contains control characters"
    if os.path.splitext(path)[1].lower() not in _LAUNCH_EXTS:
        return False, f"Only {', '.join(_LAUNCH_EXTS)} may be launched: {path}"
    if not os.path.isabs(path):
        return False, f"Path must be absolute: {path}"
    # Blockliste auch hier durchsetzen (cmd, powershell, regedit, taskkill, …)
    base = os.path.basename(path).lower()
    if os.path.splitext(base)[0] in BLOCKED_PROGRAMS or base in BLOCKED_PROGRAMS:
        return False, f"Blocked program: {path}"
    if not os.path.isfile(path):
        return False, f"File not found: {path}"
    return True, "OK"


def _validate_program(program: str) -> tuple[bool, str]:
    """Validate a program name against the whitelist and blocklist.

    Returns (is_valid, reason).
    """
    if not program or not program.strip():
        return False, "Empty program name"

    # Trim whitespace and normalise separators
    program = program.strip()

    # Normalise: take the base name without path and extension for matching
    base = os.path.basename(program).lower().strip()
    name_no_ext = os.path.splitext(base)[0].strip()

    # Check for shell metacharacters / injection patterns
    if _INJECTION_PATTERN.search(program):
        return False, f"Program name contains forbidden characters: {program}"

    # Block dangerous commands
    if name_no_ext in BLOCKED_PROGRAMS or base in BLOCKED_PROGRAMS:
        return False, f"Blocked program: {program}"

    # Build a normalised whitelist for tolerant matching (spaces, hyphens, case)
    def _norm(s: str) -> str:
        return s.lower().strip().replace("-", " ")

    norm_allowed = {_norm(p) for p in ALLOWED_PROGRAMS}
    candidates = {_norm(name_no_ext), _norm(base)}

    if not (candidates & norm_allowed):
        return False, (
            f"Program '{program}' is not in the allowed whitelist. "
            f"Add it to ALLOWED_PROGRAMS if it is safe."
        )

    return True, "OK"


class Executor:
    def __init__(self, safety: SafetyGuard, kill_event):
        self.safety = safety
        self.kill_event = kill_event
        self.os = platform.system()  # Windows, Darwin, Linux
        self._init_controllers()

    def _init_controllers(self):
        """Initialisiere Maus/Tastatur-Controller"""
        try:
            import pyautogui
            pyautogui.FAILSAFE = True  # Maus zur Ecke = Stopp
            pyautogui.PAUSE = 0.3     # Kleine Pause zwischen Aktionen
            self.pag = pyautogui
            self.mouse_available = True
            print("[Executor] PyAutoGUI initialisiert")
        except ImportError:
            print("[Executor] PyAutoGUI nicht verfügbar - Maus/Tastatur deaktiviert")
            self.pag = None
            self.mouse_available = False

    async def execute_tool(self, tool_name: str, params: dict):
        """Führt ein Tool aus"""
        if self.kill_event.is_set():
            print("[Executor] Kill-Switch aktiv, Aktion übersprungen")
            return None

        print(f"[Executor] Tool: {tool_name} | Params: {params}")

        handlers = {
            "mouse_click": self._mouse_click,
            "mouse_drag": self._mouse_drag,
            "keyboard_type": self._keyboard_type,
            "open_program": self._open_program,
            "launch_app": self._launch_app,
            "close_window": self._close_window,
            "take_screenshot": self._take_screenshot,
            "web_search": self._web_search,
            "run_command": self._run_command,
            "key_press": self._key_press,
            "scroll": self._scroll,
        }

        handler = handlers.get(tool_name)
        if handler:
            return await handler(params)
        else:
            print(f"[Executor] Unbekanntes Tool: {tool_name}")
            return {"error": f"Unknown tool: {tool_name}"}

    async def _mouse_click(self, params: dict):
        """Klickt an Position"""
        if not self.mouse_available:
            return {"error": "Maus nicht verfügbar"}

        x = params.get("x", 0)
        y = params.get("y", 0)
        button = params.get("button", "left")

        # Bildschirmgrenzen prüfen
        screen_w, screen_h = self.pag.size()
        if not (0 <= x <= screen_w and 0 <= y <= screen_h):
            return {"error": f"Position außerhalb des Bildschirms: {x},{y}"}

        # v2.7 #3b: erst sanft zur Position fahren (bessere Klick-Genauigkeit),
        # dann klicken – statt direktem click(x,y) das gern danebentrifft.
        try:
            self.pag.moveTo(x, y, duration=0.3)
        except Exception:
            pass

        if button == "double":
            self.pag.doubleClick(x, y)
        elif button == "right":
            self.pag.rightClick(x, y)
        else:
            self.pag.click(x, y)

        print(f"[Executor] Klick: ({x},{y}) {button}")
        return {"success": True}

    async def _mouse_drag(self, params: dict):
        """Drag & Drop von (x,y) nach (to_x,to_y)."""
        if not self.mouse_available:
            return {"error": "Maus nicht verfügbar"}

        x = params.get("x", 0)
        y = params.get("y", 0)
        to_x = params.get("to_x", 0)
        to_y = params.get("to_y", 0)
        duration = params.get("duration", 0.4)
        try:
            duration = max(0.1, min(3.0, float(duration)))
        except (TypeError, ValueError):
            duration = 0.4

        screen_w, screen_h = self.pag.size()
        for px, py in ((x, y), (to_x, to_y)):
            if not (0 <= px <= screen_w and 0 <= py <= screen_h):
                return {"error": f"Position außerhalb des Bildschirms: {px},{py}"}

        self.pag.moveTo(x, y)
        self.pag.dragTo(to_x, to_y, duration=duration, button="left")
        print(f"[Executor] Drag: ({x},{y}) -> ({to_x},{to_y})")
        return {"success": True}

    async def _keyboard_type(self, params: dict):
        """Tippt Text"""
        if not self.mouse_available:
            return {"error": "Tastatur nicht verfügbar"}

        text = params.get("text", "")
        press_enter = params.get("press_enter", False)

        # Sicherheitscheck für Command-Injection
        dangerous_chars = [";", "&&", "||", "|", "`", "$()"]
        for dc in dangerous_chars:
            if dc in text:
                return {"error": f"Potenziell gefährliches Zeichen: {dc}"}

        self.pag.typewrite(text, interval=0.05)
        if press_enter:
            self.pag.press('enter')

        return {"success": True}

    async def _key_press(self, params: dict):
        """Drückt eine Tastenkombination"""
        if not self.mouse_available:
            return {"error": "Tastatur nicht verfügbar"}

        keys = params.get("keys", [])
        if isinstance(keys, str):
            keys = [keys]

        self.pag.hotkey(*keys)
        return {"success": True}

    async def _scroll(self, params: dict):
        """Scrollt"""
        if not self.mouse_available:
            return {"error": "Maus nicht verfügbar"}

        amount = params.get("amount", 3)
        direction = params.get("direction", "down")

        if direction == "down":
            self.pag.scroll(-amount)
        else:
            self.pag.scroll(amount)

        return {"success": True}

    async def _open_program(self, params: dict):
        """Öffnet ein Programm"""
        program = params.get("program", "")

        # Validate against whitelist / blocklist
        valid, reason = _validate_program(program)
        if not valid:
            print(f"[Executor] Program blocked: {reason}")
            return {"error": reason}

        if self.os == "Windows":
            try:
                os.startfile(program)
                return {"success": True}
            except FileNotFoundError:
                return {"error": f"Programm nicht gefunden: '{program}'. Prüfe PATH oder Installation."}
            except OSError as e:
                return {"error": f"OS-Fehler beim Öffnen von '{program}': {e}"}

        elif self.os == "Darwin":  # macOS
            try:
                subprocess.Popen(["open", program])
                return {"success": True}
            except FileNotFoundError:
                return {"error": f"Programm nicht gefunden: '{program}' (macOS open)."}
            except OSError as e:
                return {"error": f"OS-Fehler beim Öffnen von '{program}': {e}"}

        else:  # Linux
            try:
                # Use shlex.split for safe argument parsing on non-Windows
                args = shlex.split(program)
                subprocess.Popen(args)
                return {"success": True}
            except ValueError as e:
                return {"error": f"Ungültige Programm-Syntax: {e}"}
            except FileNotFoundError:
                return {"error": f"Programm nicht gefunden: '{program}'. Prüfe PATH."}
            except OSError as e:
                return {"error": f"OS-Fehler beim Öffnen von '{program}': {e}"}

    async def _launch_app(self, params: dict):
        """v2.9: Startet ein Programm über einen vollständigen Pfad (.exe/.lnk),
        den der App-Index auf diesem System gefunden hat.

        Ergänzt _open_program (Name-Whitelist) um installierte Programme, die
        nicht auf der Whitelist stehen — ohne die Whitelist aufzuweichen, da der
        Pfad streng validiert wird (existiert, .exe/.lnk, keine Steuerzeichen).
        """
        path = params.get("path", "") or params.get("program", "")
        valid, reason = _validate_launch_path(path)
        if not valid:
            print(f"[Executor] launch_app blocked: {reason}")
            return {"error": reason}
        path = path.strip().strip('"')

        if self.os == "Windows":
            try:
                os.startfile(path)
                print(f"[Executor] App gestartet: {path}")
                return {"success": True}
            except OSError as e:
                return {"error": f"Start fehlgeschlagen für '{path}': {e}"}
        else:
            # .lnk gibt es nur unter Windows; sonst die .exe direkt ausführen
            try:
                subprocess.Popen([path])
                return {"success": True}
            except OSError as e:
                return {"error": f"Start fehlgeschlagen für '{path}': {e}"}

    async def _close_window(self, params: dict):
        """Schließt das aktive Fenster sicher per Alt+F4 (kein taskkill).

        Bewusst kein willkürliches Beenden von Prozessen: Alt+F4 schließt nur
        das fokussierte Fenster und gibt der App die Chance, sauber zu beenden
        (inkl. evtl. Speichern-Dialog), statt Daten hart zu killen.
        """
        if not self.mouse_available:
            return {"error": "Tastatur nicht verfügbar"}
        try:
            if self.os == "Darwin":
                self.pag.hotkey("command", "w")
            else:
                self.pag.hotkey("alt", "f4")
            print("[Executor] Aktives Fenster geschlossen")
            return {"success": True}
        except Exception as e:
            return {"error": f"Fenster schließen fehlgeschlagen: {e}"}

    async def _take_screenshot(self, params: dict):
        """Screenshot aufnehmen"""
        if not self.mouse_available:
            return {"error": "PyAutoGUI nicht verfügbar"}

        try:
            screenshot = self.pag.screenshot()
            path = os.path.join(tempfile.gettempdir(), "jarvis_screenshot.png")
            screenshot.save(path)
            return {"success": True, "path": path}
        except Exception as e:
            return {"error": str(e)}

    async def _web_search(self, params: dict):
        """Öffnet Websuche"""
        query = params.get("query", "")
        import webbrowser
        encoded_query = quote_plus(query)
        url = f"https://www.google.com/search?q={encoded_query}"
        webbrowser.open(url)
        return {"success": True, "url": url}

    async def _run_command(self, params: dict):
        """Führt Shell-Befehl aus (nur mit Erlaubnis!)"""
        command = params.get("command", "")

        # Sicherheitscheck
        safe, reason = self.safety.check_command(command)
        if not safe:
            return {"error": f"Sicherheitscheck fehlgeschlagen: {reason}"}

        if _CMD_INJECTION_PATTERN.search(command):
            return {"error": "Befehl enthält unsichere Shell-Metazeichen."}

        try:
            if self.os == "Windows":
                args = shlex.split(command, posix=False)
            else:
                args = shlex.split(command)
        except ValueError as e:
            return {"error": f"Ungültige Befehlssyntax: {e}"}

        try:
            result = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=30
            )
            return {
                "success": True,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {"error": "Timeout nach 30 Sekunden"}
        except FileNotFoundError:
            return {"error": f"Befehl nicht gefunden: {args[0] if args else command}"}
        except Exception as e:
            return {"error": str(e)}
