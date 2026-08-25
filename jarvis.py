"""
My Jarvis v2.8 - Just A Rather Very Intelligent System
Event-driven: voice input ONLY while the mic button is pressed
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
    print("[WARNING] 'keyboard' is missing → python -m pip install keyboard")

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

# ── kill switch ────────────────────────────────────────────────────────────
KILL_ACTIVE = threading.Event()

def activate_kill():
    if KILL_ACTIVE.is_set():
        KILL_ACTIVE.clear()
        print("[KILL SWITCH] Deactivated.")
        if _gui_ref:
            _gui_ref.send_event("kill_deactivated", {})
    else:
        KILL_ACTIVE.set()
        print("[KILL SWITCH] Every command stopped.")
        # v2.8 bug 4: tell the GUI right away (not only through the 0.2s poll in the
        # main loop) – this keeps the frontend and backend state in sync.
        if _gui_ref:
            _gui_ref.send_event("kill_switch", {})

if KEYBOARD_AVAILABLE:
    try:
        keyboard.add_hotkey("ctrl+alt+j", activate_kill)
        print("[Kill switch] Ctrl+Alt+J active.")
    except Exception as e:
        print(f"[WARNING] Hotkey: {e}")


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
        # critical: the brain must know the GUI callback, or answers go nowhere
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
                    print(f"[LOG] Flush error: {e}")
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
            print("[My Jarvis] No API key – please enter one in the interface.")
            self.gui.send_event("needs_setup", {"reason": "No API key set."})

        # greeting
        salutation  = self.config.get("salutation", "")
        hour    = datetime.now().hour
        part_of_day = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
        greeting = f"{part_of_day}{', ' + salutation if salutation else ''}. I am My Jarvis and I am ready."
        self.speech.speak(greeting)
        self.gui.send_event("message", {"role": "jarvis", "text": greeting})

        # startup suggestions (only when enabled)
        if self.config.get("suggestions_enabled", True):
            suggestions = ["Summarise the latest news",
                           "Weather for today & the next few days",
                           "Read out the stock prices"]
            for r in self.memory.get_routines():
                suggestions.append(f"Routine: {r['name']}")
            # dynamic suggestions from open to-dos
            for item in self.brain.current_todo:
                if not item.get("done"):
                    suggestions.append(f"Finish this task: {item['text']}")
            # derive a suggestion from the stored memories
            mems = self.memory.get_all_memories()
            if mems:
                suggestions.append("What have you remembered about me?")
            self.gui.send_event("startup_suggestions", {"suggestions": suggestions[:8]})

        # start wake word / clap detection (if enabled)
        if self.config.get("wake_word_enabled") or self.config.get("clap_enabled"):
            self.wake_word.start()

        print("[My Jarvis] Ready. Control it through the interface.")
        print("[My Jarvis] Microphone: press the button in the chat panel.")
        print("[My Jarvis] Kill switch: Ctrl+Alt+J")

        # ── main loop: only watch the kill switch ───────────────────────
        # every input now arrives through WebSocket events (the GUI)
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
            print("[My Jarvis] Ctrl+C – shutting down...")
        except Exception as e:
            print(f"[CRITICAL] {e}")
            import traceback; traceback.print_exc()
        finally:
            self.running = False
            # give threads time to finish
            gui_thread.join(timeout=3)

    def _on_wake_trigger(self):
        """Called by the WakeWordListener on 'Hey Jarvis' or two claps."""
        if KILL_ACTIVE.is_set():
            return
        self.gui.send_event("wake_triggered", {})
        self.gui.send_event("status", {"text": "LISTENING..."})
        self.gui.send_event("notify", {"text": "Heard 'Hey Jarvis'!"})
        # start recording (the same logic as the mic button)
        if self.gui._listen_thread and self.gui._listen_thread.is_alive():
            return
        self.gui._stop_listening.clear()
        self.gui._listen_thread = threading.Thread(
            target=self.gui._do_listen, daemon=True)
        self.gui._listen_thread.start()

    def handle_text(self, text: str):
        """Called by the GUIServer – for text AND speech."""
        if KILL_ACTIVE.is_set():
            # v2.8 bug 4: re-sync the GUI to the real (active) state, in case the
            # frontend wrongly shows "kill off".
            self.gui.send_event("kill_switch", {})
            self.gui.send_event("message", {"role":"jarvis","text":"The kill switch is active. Please deactivate it first."})
            return
        text = text.strip()
        if not text:
            return

        if len(text) > 10000:
            self.gui.send_event("message", {"role":"jarvis","text":"That message is too long (max. 10000 characters)."})
            return

        farewells = ["shut down","shutdown","goodbye","good bye","bye","exit","quit"]
        if any(w in text.lower() for w in farewells):
            salutation = self.config.get("salutation","")
            bye = f"Goodbye{', '+salutation if salutation else ''}."
            self.speech.speak(bye)
            self.gui.send_event("message", {"role":"jarvis","text": bye})
            self.running = False
            return

        if not self._has_valid_key():
            msg = "Please enter an API key in the left panel first."
            self.speech.speak(msg)
            self.gui.send_event("message", {"role":"jarvis","text": msg})
            self.gui.send_event("needs_setup", {"reason":"The API key is missing."})
            return

        self.gui.send_event("thinking", {"status": True})
        try:
            self.brain.process(text)   # synchronous – no asyncio needed
        except Exception as e:
            err = str(e)
            if "401" in err or "authentication" in err.lower():
                msg = "⚠️ The API key is invalid (401). Please enter a new one."
                self.gui.send_event("needs_setup", {"reason": "The API key is invalid."})
            elif "429" in err:
                msg = "⚠️ Rate limited – wait a moment."
            else:
                msg = f"⚠️ Error: {err[:120]}"
            print(f"[ERROR] {err}")
            self.gui.send_event("message", {"role":"jarvis","text": msg})
            self.speech.speak(msg[:200])
        finally:
            self.gui.send_event("thinking", {"status": False})


if __name__ == "__main__":
    # bug 7: if no configuration exists, initialise it now
    # (interactively on a TTY, otherwise save the defaults via the TTY guard in
    # first_setup)
    if not Config.exists():
        print("[My Jarvis] No configuration found – initialising the defaults.")
        try:
            Config.first_setup()
        except Exception as e:
            print(f"[My Jarvis] Setup error: {e} – continuing with the defaults.")
            Config.save(Config.load())
        print("[My Jarvis] Please enter the API key in the interface afterwards.")
    JARVIS().run()
