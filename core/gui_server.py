"""
JARVIS GUI Server v2.8
- start_listening / stop_listening vom Mic-Button gesteuert
- Sprache läuft in eigenem Thread, blockiert nichts
- save_config, handle_text etc.
- Port-Conflict-Handling mit Auto-Increment
- Config-Validierung bei save_config
- WebSocket-Disconnect-Logging
"""
import json, queue, threading, webbrowser, time, os, asyncio, re, socket, hashlib
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

GUI_DIR   = Path(__file__).parent.parent / "gui"
PORT_HTTP_DEFAULT = 8765
PORT_WS_DEFAULT   = 8766
MAX_PORT_RETRIES  = 10
MAX_EVENT_QUEUE   = 200

# Aktive Ports (werden beim Start gesetzt)
PORT_HTTP = PORT_HTTP_DEFAULT
PORT_WS   = PORT_WS_DEFAULT

# Erlaubte Werte für Config-Validierung
VALID_API_PROVIDERS = {"anthropic", "openai", "gemini", "nvidia", "mistral", "local"}
SPRACHE_PATTERN = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


class GUIServer:
    def __init__(self, jarvis_instance):
        self.jarvis            = jarvis_instance
        self.clients           = set()
        self.event_queue       = []
        self._queue_lock       = threading.Lock()   # Bug 7: schützt event_queue
        self.loop              = None
        self.on_client_connect = None
        self._listen_thread    = None
        self._stop_listening   = threading.Event()
        self._todo_cancel      = threading.Event()
        self._todo_index       = 0
        self._vision_stop      = threading.Event()
        self._vision_thread    = None
        self._copilot_thread   = None

    # ── send ─────────────────────────────────────────────────────────────
    def send_event(self, event_type: str, data: dict):
        message = json.dumps({"type": event_type, "data": data})
        if self.loop and self.loop.is_running() and self.clients:
            asyncio.run_coroutine_threadsafe(self._broadcast(message), self.loop)
        else:
            with self._queue_lock:
                if len(self.event_queue) >= MAX_EVENT_QUEUE:
                    self.event_queue.pop(0)
                self.event_queue.append(message)
        # Console-Fallback nur für wichtige Events
        if event_type == "message":
            print(f"[{data.get('role','?').upper()}] {data.get('text','')[:80]}")

    async def _broadcast(self, message: str):
        # Bug 8: über eine Momentaufnahme iterieren – die clients-Menge kann
        # währenddessen (an await-Punkten) durch _handle_client mutiert werden,
        # was sonst "Set changed size during iteration" auslöst.
        dead = set()
        for ws in list(self.clients):
            try:
                await ws.send(message)
            except Exception as e:
                print(f"[GUI] Client getrennt: {e}")
                dead.add(ws)
        self.clients -= dead

    # ── WebSocket handler ─────────────────────────────────────────────────
    async def _handle_client(self, websocket, path="/"):
        self.clients.add(websocket)
        print("[GUI] Browser verbunden")
        # Bug 7: Queue atomar leeren (swap), damit parallele send_event-Appends
        # zwischen Iteration und clear() nicht verloren gehen.
        with self._queue_lock:
            pending = self.event_queue
            self.event_queue = []
        for msg in pending:
            try:
                await websocket.send(msg)
            except (ConnectionResetError, OSError) as e:
                print(f"[GUI] Queued-Event-Sendefehler: {e}")
                break
        # ── v2.8 Bug 5: aktuellen Kill-Switch-Status an den (neu) verbundenen
        # Client senden. Ohne das zeigt ein neu geladenes/wieder verbundenes GUI
        # fälschlich "Kill-Switch aus", während das Backend noch gestoppt ist –
        # dann verweigert copilot_start mit "Kill-Switch ist aktiv".
        try:
            from jarvis import KILL_ACTIVE
            await websocket.send(json.dumps({
                "type": "kill_switch" if KILL_ACTIVE.is_set() else "kill_deactivated",
                "data": {},
            }))
        except Exception as e:
            print(f"[GUI] Kill-State-Sync fehlgeschlagen: {e}")
        if self.on_client_connect:
            try:
                self.on_client_connect()
            except Exception as e:
                print(f"[GUI] on_client_connect Fehler: {e}")
        try:
            async for raw in websocket:
                try: await self._handle_msg(json.loads(raw))
                except Exception as e: print(f"[GUI] Msg-Fehler: {e}")
        except (ConnectionResetError, OSError):
            pass
        except Exception as e:
            print(f"[GUI] WebSocket-Fehler: {e}")
        finally:
            self.clients.discard(websocket)
            print("[GUI] Browser getrennt")

    # ── message router ────────────────────────────────────────────────────
    async def _handle_msg(self, data: dict):
        t = data.get("type")

        # ── Mikrofon EIN ─────────────────────────────────────────────────
        if t == "start_listening":
            if self._listen_thread and self._listen_thread.is_alive():
                return  # läuft schon
            self._stop_listening.clear()
            self._listen_thread = threading.Thread(
                target=self._do_listen, daemon=True)
            self._listen_thread.start()

        # ── Mikrofon AUS ─────────────────────────────────────────────────
        elif t == "stop_listening":
            self._stop_listening.set()
            self.jarvis.speech.stop()

        # ── Text vom Chat-Eingabefeld ─────────────────────────────────────
        # KEIN user_input Event zurückschicken – Frontend zeigt Nachricht
        # bereits selbst an (verhindert doppelte Nachrichten)
        elif t == "user_text":
            text = data.get("text","").strip()
            if text:
                threading.Thread(
                    target=self.jarvis.handle_text,
                    args=(text,), daemon=True).start()

        # ── Vorschlag akzeptiert (KEIN user_input Echo – Frontend zeigt selbst) ──
        elif t == "suggestion_accepted":
            text = data.get("suggestion","").strip()
            if text:
                threading.Thread(
                    target=self.jarvis.handle_text,
                    args=(text,), daemon=True).start()

        # ── To-Do starten ──────────────────────────────────────────────────
        elif t == "todo_start":
            self.jarvis.brain.current_todo = data.get("items",[])
            self._todo_index = 0
            threading.Thread(target=self._run_todo, daemon=True).start()

        # ── To-Do fortsetzen (nach "Problem gelöst") ───────────────────────
        elif t == "todo_continue":
            self.jarvis.brain.current_todo = data.get("items",[])
            threading.Thread(target=self._run_todo, daemon=True).start()

        # ── Config speichern ──────────────────────────────────────────────
        elif t == "save_config":
            from core.config import Config
            cfg = Config.load()
            for field in ["api_provider","api_key","anrede","redeart","sprache",
                          "nvidia_model","local_url","local_model",
                          "openai_model","wohnort","tts_enabled","suggestions_enabled",
                          "tts_voice","wake_word_enabled","clap_enabled","clap_threshold",
                          "user_name","code_retries"]:
                if field in data and data[field] != "":
                    value = data[field]
                    # Validierung
                    if field == "api_provider":
                        if value not in VALID_API_PROVIDERS:
                            continue
                    elif field in ("tts_enabled", "suggestions_enabled"):
                        if not isinstance(value, bool):
                            continue
                    elif field == "sprache":
                        if not isinstance(value, str) or not SPRACHE_PATTERN.match(value):
                            continue
                    elif field == "code_retries":
                        try:
                            value = max(1, min(10, int(value)))
                        except (ValueError, TypeError):
                            continue
                    cfg[field] = value
            # Booleans explizit (auch wenn False)
            for bfield in ["tts_enabled","suggestions_enabled","wake_word_enabled","clap_enabled",
                           "memory_history","memory_auto","vision_allowed","telemetry_enabled",
                           "code_check","code_format"]:
                if bfield in data:
                    value = data[bfield]
                    if isinstance(value, bool):
                        cfg[bfield] = value
            Config.save(cfg)
            self.jarvis.config.update(cfg)
            self.jarvis.brain.reload_config(cfg)
            print(f"[Config] Gespeichert. Provider: {cfg.get('api_provider')} | Key: {'ja' if cfg.get('api_key') else 'nein'}")
            await self._broadcast(json.dumps({
                "type": "config_saved",
                "data": {
                    "provider":    cfg.get("api_provider",""),
                    "key_preview": "*"*8 + cfg.get("api_key","")[-4:] if cfg.get("api_key") else "",
                    "ok": True
                }
            }))

        # ── Screenshot analysieren ────────────────────────────────────────
        elif t == "screenshot_analyze":
            query = data.get("query", "")
            threading.Thread(
                target=self._do_screenshot_analyze,
                args=(query,), daemon=True).start()

        # ── Live-Vision Toggle ────────────────────────────────────────────
        elif t == "toggle_live_vision":
            if self._vision_thread and self._vision_thread.is_alive():
                self._vision_stop.set()
                self.send_event("live_vision_status", {"active": False})
            else:
                self._vision_stop.clear()
                self._vision_thread = threading.Thread(
                    target=self._vision_live_loop, daemon=True)
                self._vision_thread.start()
                self.send_event("live_vision_status", {"active": True})

        # ── Copilot starten (See→Think→Act PC-Steuerung) ─────────────────
        elif t == "copilot_start":
            from jarvis import KILL_ACTIVE
            if KILL_ACTIVE.is_set():
                # v2.8 Bug 4: GUI auf den echten (aktiven) Zustand re-synchronisieren.
                # Verhindert die Falle "GUI zeigt Kill aus, Copilot meldet Kill aktiv":
                # der Nutzer sieht den Kill-Switch jetzt korrekt als aktiv und kann
                # ihn gezielt deaktivieren.
                await self._broadcast(json.dumps({"type": "kill_switch", "data": {}}))
                self.send_event("copilot_done", {"status": "stopped",
                    "summary": "Kill-Switch ist aktiv – bitte zuerst deaktivieren."})
            elif self._copilot_thread and self._copilot_thread.is_alive():
                self.send_event("copilot_status", {"text": "Copilot läuft bereits.", "phase": "busy"})
            else:
                task = (data.get("task") or "").strip()
                allow_all = bool(data.get("allow_all", False))
                self._copilot_thread = threading.Thread(
                    target=self.jarvis.copilot.run,
                    args=(task, allow_all), daemon=True)
                self._copilot_thread.start()

        # ── Copilot stoppen ───────────────────────────────────────────────
        elif t == "copilot_stop":
            self.jarvis.copilot.stop()

        # ── Copilot-Handshake (Bestätigung/Checkpoint/Nutzer-Eingabe) ─────
        elif t == "copilot_response":
            decision = (data.get("decision") or "").strip().lower()
            self.jarvis.copilot.resolve(decision)

        # ── To-Do abbrechen ──────────────────────────────────────────────
        elif t == "todo_cancel":
            self._todo_cancel.set()

        # ── Wake Word Toggle ──────────────────────────────────────────────
        elif t == "toggle_wake_word":
            enabled = not self.jarvis.config.get("wake_word_enabled", False)
            self.jarvis.config["wake_word_enabled"] = enabled
            from core.config import Config
            cfg = Config.load()
            cfg["wake_word_enabled"] = enabled
            Config.save(cfg)
            if enabled or self.jarvis.config.get("clap_enabled", False):
                self.jarvis.wake_word.config = self.jarvis.config
                self.jarvis.wake_word.start()
            else:
                self.jarvis.wake_word.stop()
            self.send_event("wake_word_status", {"enabled": enabled})

        # ── Clap Detection Toggle ────────────────────────────────────────
        elif t == "toggle_clap":
            enabled = not self.jarvis.config.get("clap_enabled", False)
            self.jarvis.config["clap_enabled"] = enabled
            from core.config import Config
            cfg = Config.load()
            cfg["clap_enabled"] = enabled
            Config.save(cfg)
            if enabled or self.jarvis.config.get("wake_word_enabled", False):
                self.jarvis.wake_word.config = self.jarvis.config
                self.jarvis.wake_word.start()
            else:
                self.jarvis.wake_word.stop()
            self.send_event("clap_status", {"enabled": enabled})

        # ── Clap Threshold ───────────────────────────────────────────────
        elif t == "set_clap_threshold":
            val = data.get("value", 0.3)
            self.jarvis.config["clap_threshold"] = val
            self.jarvis.wake_word.config = self.jarvis.config

        # ── Clap Kalibrierung ────────────────────────────────────────────
        elif t == "calibrate_clap":
            def _cal():
                threshold = self.jarvis.wake_word.calibrate_clap()
                self.jarvis.config["clap_threshold"] = threshold
                from core.config import Config
                cfg = Config.load()
                cfg["clap_threshold"] = threshold
                Config.save(cfg)
                self.send_event("clap_calibrated", {"threshold": threshold})
            threading.Thread(target=_cal, daemon=True).start()

        # ── Usage stats (Nutzung + Datenschutz panes) ──────────────────────
        elif t == "get_usage_stats":
            stats = self._build_usage_stats()
            await self._broadcast(json.dumps({
                "type": "usage_stats",
                "data": {"stats": stats},
            }))

        # ── Clear all data (Datenschutz danger zone) ───────────────────────
        elif t == "clear_all_data":
            try:
                self.jarvis.memory.memories = []
                self.jarvis.memory.history = []
                self.jarvis.memory.routines = []
                self.jarvis.memory.projects = []
                self.jarvis.memory._save_json(self.jarvis.memory.memories_file, [])
                self.jarvis.memory._save_json(self.jarvis.memory.history_file, [])
                self.jarvis.memory._save_json(self.jarvis.memory.routines_file, [])
                self.jarvis.memory._save_json(self.jarvis.memory.projects_file, [])
                print("[Privacy] Alle Daten gelöscht.")
                await self._broadcast(json.dumps({"type": "data_cleared", "data": {}}))
            except Exception as e:
                print(f"[Privacy] Wipe fehlgeschlagen: {e}")
                self.send_event("notify", {"text": f"Löschen fehlgeschlagen: {e}"})

        # ── Connectors: status / save / test ──────────────────────────────
        elif t == "get_connectors_status":
            status = self._build_connectors_status()
            await self._broadcast(json.dumps({
                "type": "connectors_status",
                "data": {"connectors": status},
            }))

        elif t == "save_connector":
            name = (data.get("name") or "").strip()
            ok = self._save_connector(name, data)
            await self._broadcast(json.dumps({
                "type": "connector_saved",
                "data": {"name": name, "ok": ok},
            }))

        elif t == "test_connector":
            name = (data.get("name") or "").strip()
            ok, err = self._test_connector(name)
            await self._broadcast(json.dumps({
                "type": "connector_test",
                "data": {"name": name, "ok": ok, "error": err},
            }))

        # ── Code tools availability (Jarvis Code pane) ─────────────────────
        elif t == "get_code_tools":
            import shutil
            tools = {
                "black": bool(shutil.which("black")),
                "prettier": bool(shutil.which("prettier")),
                "node": bool(shutil.which("node")),
            }
            await self._broadcast(json.dumps({
                "type": "code_tools",
                "data": {"tools": tools},
            }))

        # ── Rest ──────────────────────────────────────────────────────────
        elif t == "permission_response":
            pass
        elif t == "todo_edit":
            self.jarvis.brain.current_todo = data.get("items",[])
        elif t == "memory_delete":
            self.jarvis.memory.delete_memory(data.get("id"))
        elif t == "routine_delete":
            self.jarvis.memory.delete_routine(data.get("id"))
        elif t == "get_memories":
            mems = self.jarvis.memory.get_all_memories()
            await self._broadcast(json.dumps({"type":"memories_data","data":{"memories":mems}}))
        elif t == "get_routines":
            routs = self.jarvis.memory.get_routines()
            await self._broadcast(json.dumps({"type":"routines_data","data":{"routines":routs}}))
        elif t == "kill_switch":
            from jarvis import KILL_ACTIVE
            KILL_ACTIVE.set()
            self._todo_cancel.set()
            self._stop_listening.set()
            self._vision_stop.set()
            self.jarvis.copilot.stop()
            self.jarvis.speech.stop_speaking()
            while not self.jarvis.speech.tts_queue.empty():
                try:
                    self.jarvis.speech.tts_queue.get_nowait()
                except queue.Empty:
                    break
            # v2.8 Bug 4: Backend ist die einzige Wahrheitsquelle – allen Clients
            # den neuen Zustand bestätigen (sonst kann ein zweites Fenster oder ein
            # Reconnect "Kill aus" zeigen während das Backend noch gestoppt ist).
            await self._broadcast(json.dumps({"type": "kill_switch", "data": {}}))
        elif t == "kill_deactivate":
            from jarvis import KILL_ACTIVE
            KILL_ACTIVE.clear()
            print("[KILL-SWITCH] Deaktiviert.")
            # v2.8 Bug 4: Deaktivierung allen Clients bestätigen (vorher wurde gar
            # kein Event gesendet → GUI und Backend konnten auseinanderlaufen).
            await self._broadcast(json.dumps({"type": "kill_deactivated", "data": {}}))

    # ── Screenshot-Analyse ──────────────────────────────────────────────────
    def _do_screenshot_analyze(self, query: str):
        self.send_event("thinking", {"status": True})
        self.send_event("status", {"text": "Screenshot wird aufgenommen..."})
        try:
            screenshot_b64 = self.jarvis.screen.take_screenshot()
            if not screenshot_b64:
                self.send_event("message", {"role": "jarvis",
                    "text": "Screenshot fehlgeschlagen. PyAutoGUI/PIL nicht verfügbar."})
                self.send_event("thinking", {"status": False})
                return

            self.send_event("status", {"text": "Analysiere Bildschirm..."})
            reply = self.jarvis.brain.analyze_screenshot(screenshot_b64, query)
            self.send_event("message", {"role": "jarvis", "text": reply})
            self.jarvis.speech.speak(reply[:300])
        except Exception as e:
            self.send_event("message", {"role": "jarvis",
                "text": f"Screenshot-Analyse fehlgeschlagen: {str(e)[:120]}"})
            print(f"[Screenshot] Fehler: {e}")
        finally:
            self.send_event("thinking", {"status": False})
            self.send_event("status", {"text": "BEREIT"})

    # ── Live-Vision Loop ────────────────────────────────────────────────────
    def _vision_live_loop(self):
        """Beobachtet den Bildschirm und gibt bei Änderungen Feedback."""
        from jarvis import KILL_ACTIVE
        last_hash = ""
        self.send_event("status", {"text": "👁 LIVE-VISION AKTIV"})

        while not self._vision_stop.is_set():
            if KILL_ACTIVE.is_set():
                break

            screenshot_b64 = self.jarvis.screen.take_screenshot(max_width=1280)
            if not screenshot_b64:
                self._vision_stop.wait(3)
                continue

            frame_hash = hashlib.md5(screenshot_b64[:2000].encode()).hexdigest()
            if frame_hash == last_hash:
                self._vision_stop.wait(2)
                continue
            last_hash = frame_hash

            try:
                reply = self.jarvis.brain.analyze_screenshot(
                    screenshot_b64,
                    "Beschreibe kurz was sich auf dem Bildschirm geändert hat. Sei präzise und knapp."
                )
                self.send_event("message", {"role": "jarvis", "text": reply})
                self.jarvis.speech.speak(reply[:200])
            except Exception as e:
                print(f"[LiveVision] Fehler: {e}")

            self._vision_stop.wait(3)

        self.send_event("live_vision_status", {"active": False})
        self.send_event("status", {"text": "✅ BEREIT"})
        print("[LiveVision] Gestoppt.")

    # ── To-Do Abarbeitung ──────────────────────────────────────────────────
    def _run_todo(self):
        """Arbeitet die To-Do Liste Schritt für Schritt ab."""
        from jarvis import KILL_ACTIVE
        self._todo_cancel.clear()
        items = self.jarvis.brain.current_todo

        for item in items:
            if KILL_ACTIVE.is_set():
                self.send_event("todo_error", {"message": "Kill-Switch wurde aktiviert."})
                return
            if self._todo_cancel.is_set():
                self.send_event("todo_complete", {})
                print("[To-Do] Abgebrochen.")
                return
            if item.get("done"):
                continue

            # Aufgabe startet
            self.send_event("todo_running", {"id": item["id"]})
            self.send_event("user_input", {"text": f"To-Do: {item['text']}"})

            try:
                result = self.jarvis.brain.run_todo_item(item["text"])
            except Exception as e:
                self.send_event("todo_error", {"message": f"Fehler bei '{item['text']}': {str(e)[:120]}"})
                return

            if self._todo_cancel.is_set():
                self.send_event("todo_complete", {})
                print("[To-Do] Abgebrochen.")
                return

            # Fehler oder Erlaubnis nötig?
            if result and result.get("status") == "error":
                self.send_event("todo_error", {
                    "message": result.get("message", f"Problem bei: {item['text']}")
                })
                return  # Stoppt – wartet auf "Problem gelöst"

            # Aufgabe erledigt
            item["done"] = True
            self.send_event("todo_done_item", {"id": item["id"]})

        # Alle erledigt
        self.send_event("todo_complete", {})
        print("[To-Do] Alle Aufgaben abgeschlossen.")

    # ── Sprach-Aufnahme Thread ─────────────────────────────────────────────
    def _do_listen(self):
        """Läuft im Hintergrund – nimmt auf bis Text erkannt oder Stop."""
        print("[STT] Aufnahme gestartet (Mic-Button)")
        self.send_event("status", {"text": "HOERE ZU..."})

        text = self.jarvis.speech.listen(stop_event=self._stop_listening)

        # Button zurücksetzen
        self.send_event("mic_done", {})

        if text and not self._stop_listening.is_set():
            print(f"[STT] Erkannt: {text}")
            self.send_event("user_input", {"text": text})
            self.jarvis.handle_text(text)
        else:
            print("[STT] Keine Eingabe erkannt")
            self.send_event("status", {"text": "BEREIT"})

    # ── Server start ──────────────────────────────────────────────────────
    def start(self):
        global PORT_HTTP, PORT_WS
        # HTTP-Server mit Port-Retry starten
        PORT_HTTP = self._start_http_with_retry()
        if PORT_HTTP == 0:
            print("[GUI] FEHLER: HTTP-Server konnte nicht gestartet werden.")
            return
        if not WS_AVAILABLE:
            print("[GUI] websockets fehlt -> python -m pip install websockets")
            return
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def _run():
            nonlocal self
            port = PORT_WS_DEFAULT
            ws_server = None
            for attempt in range(MAX_PORT_RETRIES):
                try:
                    ws_server = await websockets.serve(
                        self._handle_client,
                        "127.0.0.1",
                        port,
                        family=socket.AF_INET
                    )
                    break
                except OSError as e:
                    print(f"[GUI] WebSocket Port {port} belegt: {e}")
                    port += 1
            else:
                print(f"[GUI] FEHLER: Kein freier WebSocket-Port gefunden ({PORT_WS_DEFAULT}-{PORT_WS_DEFAULT + MAX_PORT_RETRIES - 1})")
                return

            global PORT_WS
            PORT_WS = port
            print(f"[GUI] WebSocket: ws://127.0.0.1:{PORT_WS}")
            await asyncio.sleep(0.8)
            webbrowser.open(f"http://127.0.0.1:{PORT_HTTP}/index.html")
            await asyncio.Future()

        self.loop.run_until_complete(_run())

    def _start_http_with_retry(self) -> int:
        """Startet den HTTP-Server; probiert bei Port-Konflikten bis zu MAX_PORT_RETRIES Ports."""
        os.chdir(str(GUI_DIR))

        class Q(SimpleHTTPRequestHandler):
            def log_message(self, *a): pass

        port = PORT_HTTP_DEFAULT
        for attempt in range(MAX_PORT_RETRIES):
            try:
                server = HTTPServer(("127.0.0.1", port), Q)
                # Bind erfolgreich -- serve in Hintergrund-Thread
                threading.Thread(
                    target=server.serve_forever,
                    daemon=True
                ).start()
                print(f"[GUI] HTTP-Server: http://127.0.0.1:{port}")
                return port
            except OSError as e:
                print(f"[GUI] HTTP Port {port} belegt: {e}")
                port += 1
        print(f"[GUI] FEHLER: Kein freier HTTP-Port gefunden ({PORT_HTTP_DEFAULT}-{PORT_HTTP_DEFAULT + MAX_PORT_RETRIES - 1})")
        return 0

    # ── Usage / Privacy helpers ────────────────────────────────────────────
    def _build_usage_stats(self) -> dict:
        """Aggregate stats for the Nutzung + Datenschutz panes."""
        from datetime import datetime, timedelta
        mem_stats = self.jarvis.memory.get_stats()
        history = self.jarvis.memory.history or []

        # 7-day activity bucketing
        today = datetime.now().date()
        labels = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        buckets = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            count = 0
            for h in history:
                ts = h.get("timestamp", "")
                if not ts:
                    continue
                try:
                    d = datetime.fromisoformat(ts).date()
                    if d == day:
                        count += 1
                except (ValueError, TypeError):
                    continue
            buckets.append({
                "label": labels[day.weekday()],
                "count": count,
                "date": day.isoformat(),
            })

        messages_week = sum(b["count"] for b in buckets)
        return {
            "memories": mem_stats.get("memories", 0),
            "routines": mem_stats.get("routines", 0),
            "projects": mem_stats.get("projects", 0),
            "history": mem_stats.get("history", 0),
            "messages_week": messages_week,
            "activity": buckets,
            "provider": self.jarvis.config.get("api_provider", ""),
        }

    # ── Connector helpers ──────────────────────────────────────────────────
    def _build_connectors_status(self) -> dict:
        """Read current connector config and expose connection state."""
        cfg = self.jarvis.config
        status = {}

        # Google Calendar — token presence in ~/.jarvis/google_token.json
        from pathlib import Path
        gcal_token = Path.home() / ".jarvis" / "google_token.json"
        status["calendar"] = {
            "connected": gcal_token.exists(),
            "label": "Verbunden" if gcal_token.exists() else "Nicht verbunden",
        }

        # Todoist — api token in config
        td = bool(cfg.get("todoist_token"))
        status["todoist"] = {
            "connected": td,
            "label": "Verbunden" if td else "Nicht verbunden",
        }

        # Notion — token + database id in config
        nt = bool(cfg.get("notion_token") and cfg.get("notion_database_id"))
        status["notion"] = {
            "connected": nt,
            "label": "Verbunden" if nt else "Nicht verbunden",
        }

        # Email — IMAP creds in config
        em = bool(cfg.get("email_address") and cfg.get("email_password") and cfg.get("email_imap"))
        status["email"] = {
            "connected": em,
            "label": "Verbunden" if em else "Nicht verbunden",
        }

        # Home Assistant — url + token in config
        ha = bool(cfg.get("ha_url") and cfg.get("ha_token"))
        status["homeassistant"] = {
            "connected": ha,
            "label": "Verbunden" if ha else "Nicht verbunden",
        }

        # YouTube — always available (no key needed)
        status["youtube"] = {
            "connected": cfg.get("youtube_enabled", True) is not False,
            "label": "Verfügbar",
        }

        # Research — topics list in config
        topics = cfg.get("research_topics") or []
        status["research"] = {
            "connected": bool(topics),
            "label": f"{len(topics)} Themen" if topics else "Aus",
        }

        return status

    def _save_connector(self, name: str, data: dict) -> bool:
        """Persist connector credentials/config into the global config."""
        from core.config import Config
        cfg = Config.load()
        try:
            if name == "calendar":
                creds = (data.get("creds") or "").strip()
                if creds:
                    # If it looks like a file path, copy it; otherwise treat as token
                    from pathlib import Path as _P
                    p = _P(creds).expanduser()
                    if p.exists() and p.is_file():
                        target = _P.home() / ".jarvis" / "google_credentials.json"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(p.read_bytes())
                        cfg["google_credentials_path"] = str(target)
                    else:
                        # Treat as inline token
                        token_target = _P.home() / ".jarvis" / "google_token.json"
                        token_target.parent.mkdir(parents=True, exist_ok=True)
                        token_target.write_text(creds, encoding="utf-8")
            elif name == "todoist":
                cfg["todoist_token"] = (data.get("token") or "").strip()
            elif name == "notion":
                cfg["notion_token"] = (data.get("token") or "").strip()
                cfg["notion_database_id"] = (data.get("database_id") or "").strip()
            elif name == "email":
                cfg["email_address"] = (data.get("email") or "").strip()
                cfg["email_password"] = (data.get("password") or "")
                cfg["email_imap"] = (data.get("imap") or "").strip()
                cfg["email_smtp"] = (data.get("smtp") or "").strip()
            elif name == "homeassistant":
                cfg["ha_url"] = (data.get("url") or "").strip()
                cfg["ha_token"] = (data.get("token") or "").strip()
            elif name == "research":
                topics = data.get("topics") or []
                cfg["research_topics"] = [t for t in topics if isinstance(t, str) and t.strip()]
            else:
                return False
            Config.save(cfg)
            self.jarvis.config.update(cfg)
            return True
        except Exception as e:
            print(f"[Connector] save '{name}' failed: {e}")
            return False

    def _test_connector(self, name: str):
        """Best-effort smoke test for a connector. Returns (ok, error_message)."""
        cfg = self.jarvis.config
        try:
            if name == "calendar":
                from pathlib import Path as _P
                return ((_P.home() / ".jarvis" / "google_token.json").exists(), "Token nicht vorhanden")
            if name == "todoist":
                token = cfg.get("todoist_token")
                if not token:
                    return False, "Kein Token"
                try:
                    import urllib.request, urllib.error
                    req = urllib.request.Request(
                        "https://api.todoist.com/rest/v2/projects",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    urllib.request.urlopen(req, timeout=5).read()
                    return True, ""
                except urllib.error.HTTPError as e:
                    return False, f"HTTP {e.code}"
                except Exception as e:
                    return False, str(e)[:80]
            if name == "notion":
                token = cfg.get("notion_token")
                if not token:
                    return False, "Kein Token"
                try:
                    import urllib.request, urllib.error
                    req = urllib.request.Request(
                        "https://api.notion.com/v1/users/me",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Notion-Version": "2022-06-28",
                        },
                    )
                    urllib.request.urlopen(req, timeout=5).read()
                    return True, ""
                except urllib.error.HTTPError as e:
                    return False, f"HTTP {e.code}"
                except Exception as e:
                    return False, str(e)[:80]
            if name == "email":
                if not (cfg.get("email_address") and cfg.get("email_password") and cfg.get("email_imap")):
                    return False, "Unvollständig"
                try:
                    import imaplib
                    m = imaplib.IMAP4_SSL(cfg["email_imap"], timeout=8)
                    m.login(cfg["email_address"], cfg["email_password"])
                    m.logout()
                    return True, ""
                except Exception as e:
                    return False, str(e)[:80]
            if name == "homeassistant":
                url = cfg.get("ha_url"); tok = cfg.get("ha_token")
                if not (url and tok):
                    return False, "Unvollständig"
                try:
                    import urllib.request, urllib.error
                    req = urllib.request.Request(
                        url.rstrip("/") + "/api/",
                        headers={"Authorization": f"Bearer {tok}"},
                    )
                    urllib.request.urlopen(req, timeout=5).read()
                    return True, ""
                except urllib.error.HTTPError as e:
                    return False, f"HTTP {e.code}"
                except Exception as e:
                    return False, str(e)[:80]
            return False, "Unbekannter Konnektor"
        except Exception as e:
            return False, str(e)[:80]
