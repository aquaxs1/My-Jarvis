"""
My Jarvis app index — universal program & browser detection (v2.9)
==================================================================
Goal (spec v2.9, parts 1 + 3): My Jarvis should find ANY installed or running
program — not just a fixed list.

Building blocks:
- **Running processes** searched dynamically (psutil) — part 1a
- **Installed programs** scanned from three sources and cached — part 1b
    1. the Windows registry (uninstall keys, the most reliable source)
    2. Start-menu shortcuts (*.lnk)
    3. the usual install folders (Program Files, LocalAppData)
- **Fuzzy search** (difflib + a substring fallback) — part 1c
- **Universal browser detection** (registry default + psutil + Edge) — part 3

Everything is defensive: if a source fails (missing rights, not Windows, no
psutil) it is skipped quietly instead of blowing up the call.
The cache lives in ~/.jarvis/installed_apps.json and is refreshed every 24h.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import platform
import threading
import time
from difflib import get_close_matches

logger = logging.getLogger("jarvis.app_index")

# ── constants ────────────────────────────────────────────────────────────────
CACHE_TTL          = 24 * 3600          # the cache is valid for 24h
FUZZY_CUTOFF       = 0.4                 # spec 1c: the difflib threshold
FUZZY_N            = 5                   # max hits from difflib
_IGNORE_EXES       = {                    # hide uninteresting/technical .exe files
    "unins000.exe", "uninstall.exe", "uninst.exe", "setup.exe",
    "update.exe", "updater.exe", "crashpad_handler.exe", "crashreporter.exe",
    "vcredist.exe", "vc_redist.exe", "dxsetup.exe", "python.exe", "pythonw.exe",
    "elevation_service.exe", "notification_helper.exe", "installer.exe",
}

# Browser: Prozessname → Anzeigename (Spec Teil 3)
_BROWSER_PROCS = {
    "chrome":     "Chrome",
    "firefox":    "Firefox",
    "brave":      "Brave",
    "opera":      "Opera",
    "msedge":     "Edge",
    "vivaldi":    "Vivaldi",
    "waterfox":   "Waterfox",
    "librewolf":  "LibreWolf",
    "tor":        "Tor",
    "duckduckgo": "DuckDuckGo",
}
# ProgId (Registry-Default-Browser) → Anzeigename
_PROGID_BROWSER = {
    "ChromeHTML":           "Chrome",
    "FirefoxURL":           "Firefox",
    "BraveHTML":            "Brave",
    "OperaStable":          "Opera",
    "MSEdgeHTM":            "Edge",
    "MSEdgeHTML":           "Edge",
    "AppXq0fevzme2pys62n3e0fbqa7peapykr8v": "Edge",  # Edge (Store)
    "VivaldiHTM":           "Vivaldi",
    "DuckDuckGoHTML":       "DuckDuckGo",
}

# ── Modul-Zustand (thread-safe Cache) ─────────────────────────────────────────
_LOCK = threading.RLock()
_MEM_CACHE: list | None = None          # In-Process-Cache (vermeidet Disk-Reads)
_MEM_CACHE_TS = 0.0


# ── Pfade ──────────────────────────────────────────────────────────────────────
def _jarvis_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), ".jarvis")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _cache_path() -> str:
    return os.path.join(_jarvis_dir(), "installed_apps.json")


# ── source 1: the Windows registry (uninstall keys) ──────────────────────────
def _clean_icon_path(raw: str) -> str:
    """DisplayIcon → echter .exe-Pfad. Format oft 'C:\\...\\app.exe,0'."""
    if not raw:
        return ""
    p = raw.strip().strip('"')
    # ',<index>'-Suffix abschneiden (Icon-Index)
    if "," in p:
        head = p.rsplit(",", 1)[0].strip().strip('"')
        if head.lower().endswith(".exe"):
            p = head
    return p if p.lower().endswith(".exe") else ""


def _scan_registry() -> list:
    """Reads installed programs from the uninstall keys (HKLM + HKCU,
    both the 32- and 64-bit views). Returns [{name, path, source}]."""
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return []

    apps: list = []
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    # (root, extra access flags) — cover both registry views
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    ]
    for root, extra in roots:
        try:
            base = winreg.OpenKey(root, uninstall, 0, winreg.KEY_READ | extra)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(base)[0]
        except OSError:
            count = 0
        for i in range(count):
            try:
                sub = winreg.EnumKey(base, i)
                k = winreg.OpenKey(base, sub)
            except OSError:
                continue
            try:
                name = _reg_val(winreg, k, "DisplayName")
                if not name:
                    continue
                # System-Komponenten / Updates ausblenden
                if _reg_int(winreg, k, "SystemComponent") == 1:
                    continue
                icon = _clean_icon_path(_reg_val(winreg, k, "DisplayIcon"))
                loc = _reg_val(winreg, k, "InstallLocation")
                path = icon or (loc.strip().strip('"') if loc else "")
                apps.append({"name": name.strip(), "path": path,
                             "source": "registry"})
            except OSError:
                continue
            finally:
                try:
                    winreg.CloseKey(k)
                except Exception:  # noqa: BLE001
                    pass
        try:
            winreg.CloseKey(base)
        except Exception:  # noqa: BLE001
            pass
    return apps


def _reg_val(winreg, key, value: str) -> str:
    try:
        v, _ = winreg.QueryValueEx(key, value)
        return str(v) if v is not None else ""
    except OSError:
        return ""


def _reg_int(winreg, key, value: str) -> int:
    try:
        v, _ = winreg.QueryValueEx(key, value)
        return int(v)
    except (OSError, ValueError, TypeError):
        return -1


# ── source 2: Start-menu shortcuts (*.lnk) ───────────────────────────────────
def _scan_start_menu() -> list:
    """Collects *.lnk from both Start-menu folders. os.startfile() can launch a
    .lnk directly, so the .lnk path is a valid launch target."""
    if platform.system() != "Windows":
        return []
    roots = [
        os.path.join(os.environ.get("APPDATA", ""),
                     r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                     r"Microsoft\Windows\Start Menu\Programs"),
    ]
    apps: list = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            for lnk in glob.glob(os.path.join(root, "**", "*.lnk"),
                                 recursive=True):
                name = os.path.splitext(os.path.basename(lnk))[0].strip()
                if name:
                    apps.append({"name": name, "path": lnk,
                                 "source": "startmenu"})
        except OSError:
            continue
    return apps


# ── source 3: the usual install folders (.exe one level deep) ────────────────
def _scan_program_dirs() -> list:
    """Lists .exe files one level deep in the usual program folders."""
    if platform.system() != "Windows":
        return []
    la = os.environ.get("LOCALAPPDATA", "")
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        la,
        os.path.join(la, "Programs") if la else "",
    ]
    apps: list = []
    seen_roots = set()
    for root in roots:
        if not root or root in seen_roots or not os.path.isdir(root):
            continue
        seen_roots.add(root)
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            sub = os.path.join(root, entry)
            # .exe direkt im Root
            if entry.lower().endswith(".exe"):
                _add_exe(apps, sub)
                continue
            if not os.path.isdir(sub):
                continue
            # .exe eine Ebene tief
            try:
                for f in os.listdir(sub):
                    if f.lower().endswith(".exe"):
                        _add_exe(apps, os.path.join(sub, f), folder=entry)
            except OSError:
                continue
    return apps


def _add_exe(apps: list, path: str, folder: str = "") -> None:
    exe = os.path.basename(path)
    if exe.lower() in _IGNORE_EXES:
        return
    # Anzeigename: Ordnername bevorzugen (lesbarer als roher exe-Name)
    name = folder.strip() or os.path.splitext(exe)[0].strip()
    if name:
        apps.append({"name": name, "path": path, "source": "programdir"})


# Common Windows built-ins (they live in System32, not in Program Files).
# Friendly name → exe in %WINDIR%\System32. Deliberately WITHOUT
# cmd/powershell/regedit (those are on the executor block list and are not meant
# to be launched). Some entries are the localised display names Windows itself
# shows on a non-English install, so those names are matched too.
_WINDOWS_BUILTINS = {
    "Notepad":        "notepad.exe",
    "Editor":         "notepad.exe",      # German Windows display name
    "Calculator":     "calc.exe",
    "Rechner":        "calc.exe",          # German Windows display name
    "Paint":          "mspaint.exe",
    "WordPad":        "write.exe",
    "Explorer":       "explorer.exe",
    "Datei-Explorer": "explorer.exe",      # German Windows display name
    "Task Manager":   "Taskmgr.exe",
    "Task-Manager":   "Taskmgr.exe",       # German Windows display name
    "Snipping Tool":  "SnippingTool.exe",
    "Character Map":  "charmap.exe",
    "Control Panel":  "control.exe",
    "Systemsteuerung":"control.exe",       # German Windows display name
    "Magnifier":      "magnify.exe",
    "On-Screen Keyboard": "osk.exe",
}


def _scan_windows_builtins() -> list:
    """Known Windows built-ins with their System32 paths (where present)."""
    if platform.system() != "Windows":
        return []
    sysroot = os.environ.get("WINDIR", r"C:\Windows")
    sys32 = os.path.join(sysroot, "System32")
    apps: list = []
    for name, exe in _WINDOWS_BUILTINS.items():
        path = os.path.join(sys32, exe)
        if os.path.isfile(path):
            apps.append({"name": name, "path": path, "source": "windows"})
    return apps


# ── scan + dedup + cache ──────────────────────────────────────────────────────
def _dedup(apps: list) -> list:
    """Merges duplicates (the same name, case-insensitively). Entries with a real
    .exe/.lnk path win."""
    best: dict = {}
    for a in apps:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        cur = best.get(key)
        if cur is None:
            best[key] = a
            continue
        # prefer the entry with a launchable path (.exe/.lnk)
        if _is_launchable(a.get("path")) and not _is_launchable(cur.get("path")):
            best[key] = a
    return sorted(best.values(), key=lambda x: x["name"].lower())


def _is_launchable(path: str | None) -> bool:
    if not path:
        return False
    low = path.lower()
    return low.endswith(".exe") or low.endswith(".lnk")


def scan_installed_apps() -> list:
    """Alle drei Quellen scannen, kombinieren, deduplizieren. [{name,path,source}]."""
    apps: list = []
    for fn in (_scan_registry, _scan_start_menu, _scan_program_dirs,
               _scan_windows_builtins):
        try:
            apps.extend(fn())
        except Exception as e:  # noqa: BLE001 – one source must never sink the rest
            logger.warning("[AppIndex] scan source %s failed: %s",
                           fn.__name__, e)
    result = _dedup(apps)
    logger.info("[AppIndex] %d installed programs found.", len(result))
    return result


def _load_cache() -> tuple[list | None, float]:
    """(apps, mtime) from the cache file – or (None, 0) when it is not there."""
    p = _cache_path()
    try:
        if not os.path.isfile(p):
            return None, 0.0
        mtime = os.path.getmtime(p)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("apps"), list):
            return data["apps"], mtime
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("[AppIndex] Cache is not readable: %s", e)
    return None, 0.0


def _save_cache(apps: list) -> None:
    p = _cache_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"version": "2.9", "scanned_at": time.time(),
                       "apps": apps}, f, ensure_ascii=False, indent=1)
    except OSError as e:
        logger.debug("[AppIndex] Cache is not writable: %s", e)


def get_installed_apps(force_refresh: bool = False) -> list:
    """Returns the installed programs – from the cache, otherwise a fresh scan.

    The cache (on disk + in process memory) is refreshed every 24h, or on
    force_refresh. Thread-safe.
    """
    global _MEM_CACHE, _MEM_CACHE_TS
    with _LOCK:
        now = time.time()
        # 1. the in-process cache
        if (not force_refresh and _MEM_CACHE is not None
                and (now - _MEM_CACHE_TS) < CACHE_TTL):
            return _MEM_CACHE
        # 2. the disk cache
        if not force_refresh:
            apps, mtime = _load_cache()
            if apps is not None and (now - mtime) < CACHE_TTL:
                _MEM_CACHE, _MEM_CACHE_TS = apps, now
                return apps
        # 3. a fresh scan
        apps = scan_installed_apps()
        if apps:                       # never persist an empty result
            _save_cache(apps)
        _MEM_CACHE, _MEM_CACHE_TS = apps, now
        return apps


def refresh_installed_apps() -> list:
    """Forces a re-scan and refreshes the cache."""
    return get_installed_apps(force_refresh=True)


def prewarm(background: bool = True) -> None:
    """Scan once at startup (per the spec). By default in a background thread, so
    that starting the program does not block."""
    if not background:
        get_installed_apps()
        return

    def _run():
        try:
            get_installed_apps()
        except Exception as e:  # noqa: BLE001
            logger.debug("[AppIndex] the prewarm failed: %s", e)

    threading.Thread(target=_run, name="appindex-prewarm", daemon=True).start()


# ── Teil 1c: Unscharfe Suche (fuzzy matching) ─────────────────────────────────
def find_best_match(user_term: str, all_apps: list | None = None,
                    limit: int = 3) -> list:
    """Findet die zu user_term am besten passenden installierten Programme.

    'vlc' → 'VLC media player', 'edge' → 'Microsoft Edge', 'photo' → 'Photoshop'.
    [{name,path,source}].

    Ranking (good hits beat fuzzy difflib noise):
      0. an exact name                       ("edge" == "edge")
      1. name/word starts with the term      ("photo" → "Photoshop")
      2. the term is a substring             ("edge" → "Microsoft Edge")
      3. difflib similarity (fills the rest) ("chrom" → "Chrome")
    """
    term = (user_term or "").strip().lower()
    if not term:
        return []
    if all_apps is None:
        all_apps = get_installed_apps()
    if not all_apps:
        return []

    by_name: dict = {}
    for a in all_apps:
        by_name.setdefault((a.get("name") or "").lower(), a)
    names = list(by_name.keys())

    ordered: list = []
    seen: set = set()

    def take(name: str) -> None:
        if name not in seen:
            seen.add(name)
            ordered.append(by_name[name])

    # 0. an exact hit
    if term in by_name:
        take(term)

    # 1. prefix: the name or a word in it starts with the term (shorter first)
    prefix = sorted(
        (n for n in names
         if n.startswith(term) or any(w.startswith(term) for w in n.split())),
        key=len)
    for n in prefix:
        take(n)

    # 2. substring: the term appears somewhere in the name (shorter first)
    substr = sorted((n for n in names if term in n), key=len)
    for n in substr:
        take(n)

    # 3. difflib similarity fills the remaining slots
    for m in get_close_matches(term, names, n=FUZZY_N, cutoff=FUZZY_CUTOFF):
        take(m)

    return ordered[:limit]


# ── part 1a: search running processes dynamically (psutil) ───────────────────
def list_running_processes() -> list:
    """Every running process (deduped by name). [{name, exe, pid}].
    Serves as the candidate list for the model ('which one matches X?')."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return []
    out: dict = {}
    try:
        for p in psutil.process_iter(["name", "exe", "pid"]):
            info = p.info
            name = (info.get("name") or "").strip()
            if not name or name.lower() in _IGNORE_EXES:
                continue
            # once per process name (the first PID is enough to focus it)
            key = name.lower()
            if key not in out:
                out[key] = {"name": name, "exe": info.get("exe") or "",
                            "pid": info.get("pid")}
    except Exception as e:  # noqa: BLE001
        logger.debug("[AppIndex] process_iter failed: %s", e)
    return list(out.values())


def find_running_processes(user_term: str, limit: int = 5) -> list:
    """Running processes that fuzzily match user_term. [{name, exe, pid}].

    A pure Python heuristic (substring + difflib) — no model involved. The copilot
    can additionally have the model pick from the result (spec 1a)."""
    term = (user_term or "").strip().lower()
    if not term:
        return []
    procs = list_running_processes()
    if not procs:
        return []

    # Vergleichsbasis: Prozessname OHNE .exe
    def base(n: str) -> str:
        return os.path.splitext(n)[0].lower()

    scored: list = []
    for p in procs:
        b = base(p["name"])
        if term == b or term in b or b in term:
            # exakter / Substring-Treffer zuerst
            scored.append((0 if term == b else 1, p))
    if scored:
        scored.sort(key=lambda t: t[0])
        return [p for _, p in scored][:limit]

    # difflib-Fallback auf die Prozess-Basisnamen
    base_map: dict = {}
    for p in procs:
        base_map.setdefault(base(p["name"]), p)
    matches = get_close_matches(term, list(base_map.keys()),
                                n=limit, cutoff=FUZZY_CUTOFF)
    return [base_map[m] for m in matches]


# ── Teil 3: universelle Browser-Erkennung ─────────────────────────────────────
def get_default_browser_from_registry() -> dict | None:
    """The default browser from the Windows registry. {name, progid}, or None.

    HKCU\\...\\Shell\\Associations\\UrlAssociations\\https\\UserChoice → ProgId.
    """
    if platform.system() != "Windows":
        return None
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return None
    key = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Shell\Associations"
           r"\UrlAssociations\https\UserChoice")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            progid, _ = winreg.QueryValueEx(k, "ProgId")
    except OSError:
        return None
    progid = str(progid or "")
    # ProgId → display name (cover prefix matches too)
    name = _PROGID_BROWSER.get(progid)
    if not name:
        for pid_prefix, n in _PROGID_BROWSER.items():
            if progid.startswith(pid_prefix[:8]):
                name = n
                break
    if not name:
        return None
    return {"name": name, "progid": progid}


def find_running_browser() -> dict | None:
    """The first running browser window found. {name, exe, pid}, or None."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return None
    try:
        for p in psutil.process_iter(["name", "exe", "pid"]):
            pname = (p.info.get("name") or "").lower()
            for proc_key, label in _BROWSER_PROCS.items():
                if proc_key in pname:
                    return {"name": label, "exe": p.info.get("exe") or "",
                            "pid": p.info.get("pid"), "running": True}
    except Exception as e:  # noqa: BLE001
        logger.debug("[AppIndex] the browser process scan failed: %s", e)
    return None


def find_any_browser() -> dict:
    """Finds ANY usable browser (spec part 3).

    Order:
      1. Is a browser already running?  → use it (focus the window)
      2. The default browser (registry) → start it
      3. Any browser from the app list  → start it
      4. Last resort: Edge (always present on Windows)

    Returns {name, path, running, pid?}. 'path' is – where it can be resolved – a
    launchable .exe/.lnk path, otherwise empty (the name alone is then enough for
    the search).
    """
    # 1. a browser that is already running
    running = find_running_browser()
    if running:
        return running

    apps = get_installed_apps()

    # 2. the default browser from the registry
    default = get_default_browser_from_registry()
    if default:
        match = find_best_match(default["name"], apps, limit=1)
        path = match[0]["path"] if match and _is_launchable(match[0].get("path")) else ""
        return {"name": default["name"], "path": path, "running": False}

    # 3. any installed browser (in preference order)
    for label in ("Chrome", "Brave", "Opera", "Firefox", "Vivaldi", "Edge"):
        match = find_best_match(label, apps, limit=1)
        if match:
            path = match[0]["path"] if _is_launchable(match[0].get("path")) else ""
            return {"name": label, "path": path, "running": False}

    # 4. Edge is always present on Windows
    return {"name": "Edge", "path": "", "running": False}


def find_launch_path(user_term: str) -> dict | None:
    """Bequemer Einzeltreffer: bester installierter Treffer MIT startbarem Pfad.
    {name, path, source} oder None."""
    for app in find_best_match(user_term, limit=FUZZY_N):
        if _is_launchable(app.get("path")):
            return app
    return None
