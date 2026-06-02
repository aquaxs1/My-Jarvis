"""
JARVIS v2.8 - Just A Rather Very Intelligent System
Event-gesteuert: Spracheingabe NUR wenn Mic-Button gedrückt
"""
import sys
import threading
import time
import queue
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── GUI-Logger ─────────────────────────────────────────────────────────────
_log_queue = queue.Queue()
_gui_ref   = None

class GUILogger:
    def __init__(self, original):
        self._orig = original
        self._lock = threading.Lock()
    def write(self, msg):
        with self._lock:
            self._orig.write(msg)
            if msg.strip():
                _log_queue.put(msg.strip())
                if _gui_ref:
                    try: _gui_ref.send_event("log", {"text": msg.strip()})
                    except (AttributeError, TypeError, RuntimeError) as _e:
                        self._orig.write(f"[GUILogger] Fehler: {_e}\n")
    def flush(self): self._orig.flush()

sys.stdout = GUILogger(sys.stdout)
sys.stderr = GUILogger(sys.stderr)

# ── Imports ────────────────────────────────────────────────────────────────
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False
    print("[WARNUNG] 'keyboard' fehlt → python -m pip install keyboard")

from core.config     import Config
from core.speech     import SpeechEngine
from core.brain      import Brain
from core.safety     import SafetyGuard
from core.executor   import Executor
from core.screen     import ScreenWatcher
from core.wake_word  import WakeWordListener
from core.copilot    import Copilot
from memory.memory_store import MemoryStore
from core.gui_server import GUIServer

# ── Kill-Switch ────────────────────────────────────────────────────────────
KILL_ACTIVE = threading.Event()

def activate_kill():
    if KILL_ACTIVE.is_set():
        KILL_ACTIVE.clear()
        print("[KILL-SWITCH] Deaktiviert.")
        if _gui_ref:
            _gui_ref.send_event("kill_deactivated", {})
    else:
        KILL_ACTIVE.set()
        print("[KILL-SWITCH] Alle Befehle gestoppt.")
        # v2.8 Bug 4: GUI sofort informieren (nicht erst über den 0.2s-Poll im
        # Haupt-Loop) – hält Frontend und Backend-Zustand synchron.
        if _gui_ref:
            _gui_ref.send_event("kill_switch", {})

if KEYBOARD_AVAILABLE:
    try:
        keyboard.add_hotkey("ctrl+alt+j", activate_kill)
        print("[Kill-Switch] Strg+Alt+J aktiv.")
    except Exception as e:
        print(f"[WARNUNG] Hotkey: {e}")


class JARVIS:
    def __init__(self):
        self.config   = Config.load()
        self.memory   = MemoryStore()
        self.speech   = SpeechEngine(self.config)
        self.safety   = SafetyGuard()
        self.executor = Executor(self.safety, KILL_ACTIVE)
        self.screen   = ScreenWatcher()
        self.brain    = Brain(self.config, self.memory, self.executor, self.screen, KILL_ACTIVE)
        self.gui      = GUIServer(self)
        # Kritisch: Brain muss GUI-Callback kennen sonst landen Antworten im Nichts
        self.brain.set_gui_callback(self.gui.send_event)
        # Copilot: See→Think→Act PC-Steuerung (nutzt Brain-Vision + Executor)
        self.copilot  = Copilot(self.brain, self.executor, self.screen,
                                KILL_ACTIVE, self.gui.send_event)
        self.wake_word = WakeWordListener(self.config, self._on_wake_trigger)
        self.running  = False

        global _gui_ref
        _gui_ref = self.gui

        def _flush():
            while not _log_queue.empty():
                try:
                    self.gui.send_event("log", {"text": _log_queue.get_nowait()})
                except queue.Empty:
                    break
                except Exception as e:
                    print(f"[LOG] Flush-Fehler: {e}")
                    break
        self.gui.on_client_connect = _flush

    def _has_valid_key(self):
        k = self.config.get("api_key", "")
        return bool(k and len(k) > 10)

    def run(self):
        self.running = True

        # GUI starten
        gui_thread = threading.Thread(target=self.gui.start, daemon=True)
        gui_thread.start()
        time.sleep(1.8)

        # Config ans GUI
        self.gui.send_event("config_update", {
            "api_provider":  self.config.get("api_provider", ""),
            "api_key_set":   self._has_valid_key(),
            "api_key_preview": ("*"*8 + self.config.get("api_key","")[-4:]) if self._has_valid_key() else ""
        })

        if not self._has_valid_key():
            print("[JARVIS] Kein API-Key – bitte im Interface eingeben.")
            self.gui.send_event("needs_setup", {"reason": "Kein API-Key gesetzt."})

        # Begrüßung
        anrede  = self.config.get("anrede", "")
        hour    = datetime.now().hour
        tages   = "Guten Morgen" if hour < 12 else ("Guten Tag" if hour < 18 else "Guten Abend")
        greeting = f"{tages}{', ' + anrede if anrede else ''}. Ich bin JARVIS und bereit."
        self.speech.speak(greeting)
        self.gui.send_event("message", {"role": "jarvis", "text": greeting})

        # Startup-Vorschläge (nur wenn aktiviert)
        if self.config.get("suggestions_enabled", True):
            suggestions = ["Aktuelle Nachrichten zusammenfassen",
                           "Wetter für heute & die nächsten Tage",
                           "Aktienpreise vorlesen"]
            for r in self.memory.get_routines():
                suggestions.append(f"Routine: {r['name']}")
            # Dynamische Vorschläge aus offenen To-Dos
            for item in self.brain.current_todo:
                if not item.get("done"):
                    suggestions.append(f"Aufgabe erledigen: {item['text']}")
            # Aus gespeicherten Erinnerungen einen Vorschlag ableiten
            mems = self.memory.get_all_memories()
            if mems:
                suggestions.append("Was hast du dir über mich gemerkt?")
            self.gui.send_event("startup_suggestions", {"suggestions": suggestions[:8]})

        # Wake Word / Clap Detection starten (falls aktiviert)
        if self.config.get("wake_word_enabled") or self.config.get("clap_enabled"):
            self.wake_word.start()

        print("[JARVIS] Bereit. Steuerung über das Interface.")
        print("[JARVIS] Mikrofon: Knopf im Chat-Panel drücken.")
        print("[JARVIS] Kill-Switch: Strg+Alt+J")

        # ── Haupt-Loop: nur Kill-Switch überwachen ──────────────────────
        # Alle Eingaben kommen jetzt über WebSocket-Events (GUI)
        try:
            while self.running:
                KILL_ACTIVE.wait(timeout=0.2)

                if KILL_ACTIVE.is_set():
                    self.speech.stop_speaking()
                    while not self.speech.tts_queue.empty():
                        try:
                            self.speech.tts_queue.get_nowait()
                        except queue.Empty:
                            break
                    self.gui.send_event("kill_switch", {})
                    while KILL_ACTIVE.is_set() and self.running:
                        KILL_ACTIVE.wait(timeout=0.2)

        except KeyboardInterrupt:
            print("[JARVIS] Strg+C – beende...")
        except Exception as e:
            print(f"[KRITISCH] {e}")
            import traceback; traceback.print_exc()
        finally:
            self.running = False
            # give threads time to finish
            gui_thread.join(timeout=3)

    def _on_wake_trigger(self):
        """Wird vom WakeWordListener aufgerufen wenn 'Hey Jarvis' oder 2x Klatschen erkannt."""
        if KILL_ACTIVE.is_set():
            return
        self.gui.send_event("wake_triggered", {})
        self.gui.send_event("status", {"text": "HÖRE ZU..."})
        self.gui.send_event("notify", {"text": "Hey Jarvis erkannt!"})
        # Starte Mikrofon-Aufnahme (gleiche Logik wie Mic-Button)
        if self.gui._listen_thread and self.gui._listen_thread.is_alive():
            return
        self.gui._stop_listening.clear()
        self.gui._listen_thread = threading.Thread(
            target=self.gui._do_listen, daemon=True)
        self.gui._listen_thread.start()

    def handle_text(self, text: str):
        """Wird von GUIServer aufgerufen – für Text UND Sprache."""
        if KILL_ACTIVE.is_set():
            # v2.8 Bug 4: GUI auf den echten (aktiven) Zustand re-synchronisieren,
            # falls Frontend fälschlich "Kill aus" zeigt.
            self.gui.send_event("kill_switch", {})
            self.gui.send_event("message", {"role":"jarvis","text":"Kill-Switch ist aktiv. Bitte zuerst deaktivieren."})
            return
        text = text.strip()
        if not text:
            return

        if len(text) > 10000:
            self.gui.send_event("message", {"role":"jarvis","text":"Nachricht zu lang (max. 10000 Zeichen)."})
            return

        beenden = ["beenden","auf wiedersehen","tschüss","goodbye","exit","quit"]
        if any(w in text.lower() for w in beenden):
            anrede = self.config.get("anrede","")
            bye = f"Auf Wiedersehen{', '+anrede if anrede else ''}."
            self.speech.speak(bye)
            self.gui.send_event("message", {"role":"jarvis","text": bye})
            self.running = False
            return

        if not self._has_valid_key():
            msg = "Bitte zuerst einen API-Key im linken Panel eingeben."
            self.speech.speak(msg)
            self.gui.send_event("message", {"role":"jarvis","text": msg})
            self.gui.send_event("needs_setup", {"reason":"API-Key fehlt."})
            return

        self.gui.send_event("thinking", {"status": True})
        try:
            self.brain.process(text)   # synchron – kein asyncio nötig
        except Exception as e:
            err = str(e)
            if "401" in err or "authentication" in err.lower():
                msg = "⚠️ API-Key ungültig (401). Bitte neuen Key eingeben."
                self.gui.send_event("needs_setup", {"reason": "API-Key ungültig."})
            elif "429" in err:
                msg = "⚠️ Rate-Limit – kurz warten."
            else:
                msg = f"⚠️ Fehler: {err[:120]}"
            print(f"[FEHLER] {err}")
            self.gui.send_event("message", {"role":"jarvis","text": msg})
            self.speech.speak(msg[:200])
        finally:
            self.gui.send_event("thinking", {"status": False})


if __name__ == "__main__":
    # Bug 7: Wenn keine Konfiguration existiert, jetzt initialisieren
    # (interaktiv im TTY, sonst Defaults speichern via TTY-Guard in first_setup)
    if not Config.exists():
        print("[JARVIS] Keine Konfiguration gefunden – initialisiere Defaults.")
        try:
            Config.first_setup()
        except Exception as e:
            print(f"[JARVIS] Setup-Fehler: {e} – fahre mit Defaults fort.")
            Config.save(Config.load())
        print("[JARVIS] Bitte API-Key anschließend im Interface eingeben.")
    JARVIS().run()
