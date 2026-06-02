"""
JARVIS App-Index — universelle Programm- & Browser-Erkennung (v2.9)
===================================================================
Ziel (Spec v2.9, Teil 1 + 3): JARVIS soll JEDES installierte oder laufende
Programm finden — nicht nur eine feste Liste.

Drei Bausteine:
- **Laufende Prozesse** dynamisch durchsuchen (psutil) — Teil 1a
- **Installierte Programme** aus drei Quellen scannen, cachen — Teil 1b
    1. Windows-Registry (Uninstall-Keys, zuverlässigste Quelle)
    2. Startmenü-Verknüpfungen (*.lnk)
    3. Bekannte Installations-Ordner (Program Files, LocalAppData)
- **Unscharfe Suche** (difflib + Substring-Fallback) — Teil 1c
- **Universelle Browser-Erkennung** (Registry-Default + psutil + Edge) — Teil 3

Alles defensiv: schlägt eine Quelle fehl (fehlende Rechte, kein Windows,
kein psutil), wird sie still übersprungen statt den Aufruf zu sprengen.
Der Cache liegt in ~/.jarvis/installed_apps.json und wird alle 24h erneuert.
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

# ── Konstanten ───────────────────────────────────────────────────────────────
CACHE_TTL          = 24 * 3600          # Cache 24h gültig
FUZZY_CUTOFF       = 0.4                 # Spec 1c: difflib-Schwelle
FUZZY_N            = 5                   # max. Treffer aus difflib
_IGNORE_EXES       = {                    # uninteressante/technische .exe ausblenden
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


# ── Quelle 1: Windows-Registry (Uninstall-Keys) ──────────────────────────────
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
    """Liest installierte Programme aus den Uninstall-Keys (HKLM + HKCU,
    32- und 64-Bit-View). Liefert [{name, path, source}]."""
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return []

    apps: list = []
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    # (root, zusätzliche access-flags) — beide Registry-Views abdecken
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


# ── Quelle 2: Startmenü-Verknüpfungen (*.lnk) ────────────────────────────────
def _scan_start_menu() -> list:
    """Sammelt *.lnk aus den beiden Startmenü-Ordnern. os.startfile() kann
    .lnk direkt starten, daher ist der .lnk-Pfad ein gültiges Start-Ziel."""
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


# ── Quelle 3: Bekannte Installations-Ordner (.exe eine Ebene tief) ───────────
def _scan_program_dirs() -> list:
    """Listet .exe-Dateien eine Ebene tief in den üblichen Programm-Ordnern."""
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


# Häufige Windows-Bordmittel (liegen in System32, nicht in Program Files).
# Friendly-Name → exe in %WINDIR%\System32. Bewusst OHNE cmd/powershell/regedit
# (die sind in der Executor-Blockliste und nicht zum Starten gedacht).
_WINDOWS_BUILTINS = {
    "Notepad":        "notepad.exe",
    "Editor":         "notepad.exe",      # dt. Anzeigename
    "Calculator":     "calc.exe",
    "Rechner":        "calc.exe",          # dt. Anzeigename
    "Paint":          "mspaint.exe",
    "WordPad":        "write.exe",
    "Explorer":       "explorer.exe",
    "Datei-Explorer": "explorer.exe",
    "Task Manager":   "Taskmgr.exe",
    "Task-Manager":   "Taskmgr.exe",
    "Snipping Tool":  "SnippingTool.exe",
    "Character Map":  "charmap.exe",
    "Control Panel":  "control.exe",
    "Systemsteuerung":"control.exe",
    "Magnifier":      "magnify.exe",
    "On-Screen Keyboard": "osk.exe",
}


def _scan_windows_builtins() -> list:
    """Bekannte Windows-Bordmittel mit ihren System32-Pfaden (wenn vorhanden)."""
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


# ── Scan + Dedup + Cache ──────────────────────────────────────────────────────
def _dedup(apps: list) -> list:
    """Duplikate (gleicher Name, case-insensitiv) zusammenführen. Einträge mit
    echtem .exe/.lnk-Pfad werden bevorzugt."""
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
        # bevorzuge Eintrag mit startbarem Pfad (.exe/.lnk)
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
        except Exception as e:  # noqa: BLE001 – einzelne Quelle darf nie alles kippen
            logger.warning("[AppIndex] Scan-Quelle %s fehlgeschlagen: %s",
                           fn.__name__, e)
    result = _dedup(apps)
    logger.info("[AppIndex] %d installierte Programme gefunden.", len(result))
    return result


def _load_cache() -> tuple[list | None, float]:
    """(apps, mtime) aus der Cache-Datei – oder (None, 0) wenn nicht vorhanden."""
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
        logger.debug("[AppIndex] Cache nicht lesbar: %s", e)
    return None, 0.0


def _save_cache(apps: list) -> None:
    p = _cache_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"version": "2.9", "scanned_at": time.time(),
                       "apps": apps}, f, ensure_ascii=False, indent=1)
    except OSError as e:
        logger.debug("[AppIndex] Cache nicht schreibbar: %s", e)


def get_installed_apps(force_refresh: bool = False) -> list:
    """Installierte Programme zurückgeben – aus Cache, sonst frisch scannen.

    Cache (Disk + Prozess-Speicher) wird alle 24h oder bei force_refresh erneuert.
    Thread-safe.
    """
    global _MEM_CACHE, _MEM_CACHE_TS
    with _LOCK:
        now = time.time()
        # 1. In-Process-Cache
        if (not force_refresh and _MEM_CACHE is not None
                and (now - _MEM_CACHE_TS) < CACHE_TTL):
            return _MEM_CACHE
        # 2. Disk-Cache
        if not force_refresh:
            apps, mtime = _load_cache()
            if apps is not None and (now - mtime) < CACHE_TTL:
                _MEM_CACHE, _MEM_CACHE_TS = apps, now
                return apps
        # 3. Frisch scannen
        apps = scan_installed_apps()
        if apps:                       # leeres Ergebnis nicht persistieren
            _save_cache(apps)
        _MEM_CACHE, _MEM_CACHE_TS = apps, now
        return apps


def refresh_installed_apps() -> list:
    """Erzwingt einen Neu-Scan und aktualisiert den Cache."""
    return get_installed_apps(force_refresh=True)


def prewarm(background: bool = True) -> None:
    """Beim Start einmalig scannen (Spec). Standardmäßig im Hintergrund-Thread,
    damit der Programmstart nicht blockiert."""
    if not background:
        get_installed_apps()
        return

    def _run():
        try:
            get_installed_apps()
        except Exception as e:  # noqa: BLE001
            logger.debug("[AppIndex] Prewarm fehlgeschlagen: %s", e)

    threading.Thread(target=_run, name="appindex-prewarm", daemon=True).start()


# ── Teil 1c: Unscharfe Suche (fuzzy matching) ─────────────────────────────────
def find_best_match(user_term: str, all_apps: list | None = None,
                    limit: int = 3) -> list:
    """Findet die zu user_term am besten passenden installierten Programme.

    'vlc' → 'VLC media player', 'edge' → 'Microsoft Edge', 'photo' → 'Photoshop'.
    [{name,path,source}].

    Ranking (gute Treffer schlagen unscharfes difflib-Rauschen):
      0. exakter Name                        ("edge" == "edge")
      1. Name/Wort beginnt mit dem Begriff   ("photo" → "Photoshop")
      2. Begriff ist Teilstring              ("edge" → "Microsoft Edge")
      3. difflib-Ähnlichkeit (füllt auf)     ("chrom" → "Chrome")
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

    # 0. exakter Treffer
    if term in by_name:
        take(term)

    # 1. Präfix: Name oder ein Wort darin beginnt mit dem Begriff (kürzere zuerst)
    prefix = sorted(
        (n for n in names
         if n.startswith(term) or any(w.startswith(term) for w in n.split())),
        key=len)
    for n in prefix:
        take(n)

    # 2. Substring: Begriff kommt irgendwo im Namen vor (kürzere zuerst)
    substr = sorted((n for n in names if term in n), key=len)
    for n in substr:
        take(n)

    # 3. difflib-Ähnlichkeit füllt die restlichen Plätze
    for m in get_close_matches(term, names, n=FUZZY_N, cutoff=FUZZY_CUTOFF):
        take(m)

    return ordered[:limit]


# ── Teil 1a: Laufende Prozesse dynamisch durchsuchen (psutil) ─────────────────
def list_running_processes() -> list:
    """Alle laufenden Prozesse (deduped nach Name). [{name, exe, pid}].
    Dient als Kandidatenliste für das Modell ('welcher passt zu X?')."""
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
            # je Prozessname nur einmal (erste PID reicht zum Fokussieren)
            key = name.lower()
            if key not in out:
                out[key] = {"name": name, "exe": info.get("exe") or "",
                            "pid": info.get("pid")}
    except Exception as e:  # noqa: BLE001
        logger.debug("[AppIndex] process_iter fehlgeschlagen: %s", e)
    return list(out.values())


def find_running_processes(user_term: str, limit: int = 5) -> list:
    """Laufende Prozesse, die unscharf zu user_term passen. [{name, exe, pid}].

    Reine Python-Heuristik (Substring + difflib) — ohne Modell. Der Copilot kann
    das Ergebnis zusätzlich vom Modell entscheiden lassen (Spec 1a)."""
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
    """Standard-Browser aus der Windows-Registry. {name, progid} oder None.

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
    # ProgId → Anzeigename (auch Präfix-Matches abdecken)
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
    """Erstbestes laufendes Browser-Fenster. {name, exe, pid} oder None."""
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
        logger.debug("[AppIndex] Browser-Prozess-Scan fehlgeschlagen: %s", e)
    return None


def find_any_browser() -> dict:
    """Findet IRGENDEINEN nutzbaren Browser (Spec Teil 3).

    Reihenfolge:
      1. Läuft schon ein Browser?     → den nutzen (Fenster fokussieren)
      2. Standard-Browser (Registry)  → starten
      3. Irgendeiner aus App-Liste    → starten
      4. Letzter Ausweg: Edge (immer auf Windows vorhanden)

    Returns {name, path, running, pid?}. 'path' ist – wenn auflösbar – ein
    startbarer .exe/.lnk-Pfad, sonst leer (dann reicht der Name für die Suche).
    """
    # 1. Bereits laufender Browser
    running = find_running_browser()
    if running:
        return running

    apps = get_installed_apps()

    # 2. Standard-Browser aus Registry
    default = get_default_browser_from_registry()
    if default:
        match = find_best_match(default["name"], apps, limit=1)
        path = match[0]["path"] if match and _is_launchable(match[0].get("path")) else ""
        return {"name": default["name"], "path": path, "running": False}

    # 3. Irgendeinen installierten Browser (Präferenz-Reihenfolge)
    for label in ("Chrome", "Brave", "Opera", "Firefox", "Vivaldi", "Edge"):
        match = find_best_match(label, apps, limit=1)
        if match:
            path = match[0]["path"] if _is_launchable(match[0].get("path")) else ""
            return {"name": label, "path": path, "running": False}

    # 4. Edge ist auf Windows immer vorhanden
    return {"name": "Edge", "path": "", "running": False}


def find_launch_path(user_term: str) -> dict | None:
    """Bequemer Einzeltreffer: bester installierter Treffer MIT startbarem Pfad.
    {name, path, source} oder None."""
    for app in find_best_match(user_term, limit=FUZZY_N):
        if _is_launchable(app.get("path")):
            return app
    return None
