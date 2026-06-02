"""
JARVIS Brain v2.8 – synchron
- Denkschritte (thinking_step Events)
- Standort wird verwendet, nicht erfragt
- Memory: nur Key=Value extrahieren
- To-Do Ausführung
- v2.0: Specific exceptions, timeouts, response validation,
        configurable models, constants
"""
import json, logging, time, requests, anthropic, httpx
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Constants (Bug 1.21) ─────────────────────────────────────────────────
MAX_RESPONSE_TOKENS = 2048
CLASSIFIER_MAX_TOKENS = 400
MEMORY_EXTRACT_MAX_TOKENS = 60

SYSTEM_PROMPTS = {
    "professionell": "Du bist JARVIS, ein hochentwickelter KI-Assistent. Antworte präzise auf Deutsch. Fachbegriffe sind erwünscht.",
    "normal":        "Du bist JARVIS, ein freundlicher KI-Assistent. Antworte klar und verständlich auf Deutsch.",
    "jugendlich":    "Du bist JARVIS, ein cooler KI-Assistent. Jugendslang (bro, gng, wallah, no cap). Antworte auf Deutsch.",
}

# Language display names – could be externalized to a config file or JSON resource (Bug 1.17)
LANG_NAMES = {
    "de-DE":"Deutsch","en-US":"English","fr-FR":"Französisch",
    "es-ES":"Spanisch","it-IT":"Italienisch","tr-TR":"Türkisch",
}

CLASSIFIER = """Analysiere die Benutzeranfrage. Antworte NUR mit JSON (keine Backticks, kein Markdown):
{
  "agent": "conversation|computer_control|web_search|coding|analysis|planning|memory|system|screen_vision|calendar|tasks|email|briefing|document|smarthome|youtube|social_media|decision|finance|research",
  "komplex": false,
  "sicherheitsrisiko": "keine",
  "braucht_erlaubnis": false,
  "erlaubnis_grund": "",
  "denkschritte": ["kurzer Schritt 1", "kurzer Schritt 2"],
  "zusammenfassung": "kurze Beschreibung"
}
Agenten-Zuordnung:
- calendar: Termine, Kalender, "was habe ich morgen", Termin erstellen
- tasks: Aufgaben, To-Do, Todoist, Notion Tasks
- email: E-Mail, Posteingang, Antwort schreiben
- briefing: Briefing, Tagesüberblick, "gib mir mein Briefing"
- document: PDF lesen, Dokument analysieren, Datei zusammenfassen
- smarthome: Licht, Temperatur, Musik, Smart Home
- youtube: YouTube-Link, Video zusammenfassen
- social_media: LinkedIn-Post, Twitter-Thread, Newsletter schreiben
- decision: "soll ich", "was ist besser", "hilf mir entscheiden", Pro/Kontra
- finance: Ausgaben, Budget, "ich habe X Euro ausgegeben"
- research: Recherche, Paper, arXiv, Hacker News, Forschung
Die denkschritte sollen 2-4 kurze Stichpunkte sein wie du vorgehst.
Anfrage: """

# Prompt um Key-Infos für Memory zu extrahieren
MEMORY_EXTRACT = """Extrahiere aus dieser Nutzer-Nachricht die wichtigste zu merkende Information als kurzes Key=Value Paar.
Beispiele:
"Merk dir dass ich Sebastian heiße" -> Name=Sebastian
"Denk daran dass mein Hund Rex heißt" -> Hund=Rex
"Ich wohne in Berlin" -> Wohnort=Berlin
"Merke dir mein Lieblingsessen ist Pizza" -> Lieblingsessen=Pizza
Antworte NUR mit dem Key=Value Paar, nichts anderes. Nachricht: """


def _json_from_text(raw):
    """Extrahiert das erste JSON-Objekt aus einer Modell-Antwort – tolerant ggü.
    Markdown-Fences und Text drumherum. Gibt dict oder None zurück.

    v3.0: nötig, damit die Klassifizierung auch mit Providern funktioniert, die
    (anders als Anthropic-Haiku) gern Prosa um das JSON herum schreiben.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if "```" in s:
        for part in s.split("```"):
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                s = p
                break
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: erstes balanciertes {...} herausschneiden
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except (json.JSONDecodeError, ValueError):
                        return None
    return None


# ── v3.0 Speed: Konversations-Schnellpfad (KEIN API-Call) ─────────────────────
# Stichwörter, die einen Spezial-Agenten andeuten. Fehlt JEDES davon, ist die
# Anfrage mit sehr hoher Wahrscheinlichkeit normale Konversation und wir sparen
# uns den kompletten Klassifizierungs-Roundtrip (spürbar schnellere Antworten –
# auf NVIDIA/OpenAI besonders, weil dort der Classifier sonst das große
# Hauptmodell bemühen müsste). Konservativ: im Zweifel wird klassifiziert.
_AGENT_KEYWORDS = (
    # calendar
    "termin", "kalender", "meeting", "calendar", "verabred",
    # tasks
    "aufgabe", "to-do", "todo", "to do", "todoist", "notion", "task",
    # email
    "e-mail", "email", "mail", "posteingang", "inbox", "postfach",
    # briefing
    "briefing", "tagesüberblick", "überblick", "tagesablauf",
    # document
    "pdf", "dokument", "datei", ".docx", ".txt", "zusammenfass",
    # smarthome
    "licht", "lampe", "temperatur", "heizung", "thermostat", "smart home",
    "smarthome", "steckdose", "rollladen", "rollo",
    # media / youtube
    "youtube", "spotify", "musik", "video", "playlist", "abspielen",
    # social media
    "linkedin", "twitter", "tweet", "thread", "newsletter", "instagram",
    # decision
    "soll ich", "was ist besser", "pro und kontra", "pro/kontra",
    "hilf mir entscheiden", "entscheidung", "entscheiden",
    # finance
    "ausgegeben", "ausgabe", "budget", "finanzen", "bezahlt", "gekostet",
    "kosten", "euro", "€", "rechnung", "aktie", "kurs",
    # research
    "recherche", "paper", "arxiv", "hacker news", "forschung", "studie",
    # web search / aktuelle Infos
    "google", "suche", "such ", "search", "im internet", "nachschlagen",
    "wikipedia", "wetter", "nachrichten",
    # screen / computer control
    "bildschirm", "screenshot", "screen", "klick", "öffne", "starte",
    "computer", "steuere",
)


def _is_simple_conversation(query: str) -> bool:
    """True, wenn die Anfrage KEIN Spezial-Agenten-Stichwort enthält und damit
    sehr wahrscheinlich normale Konversation ist (→ Classifier-Call überspringen)."""
    t = (query or "").lower()
    if not t.strip():
        return True
    return not any(kw in t for kw in _AGENT_KEYWORDS)


def default_classification(query: str) -> dict:
    """Standard-Klassifizierung 'conversation' ohne API-Call (Schnellpfad)."""
    return {"agent": "conversation", "komplex": False, "sicherheitsrisiko": "keine",
            "braucht_erlaubnis": False, "erlaubnis_grund": "", "denkschritte": [],
            "zusammenfassung": query}


class _RateLimiter:
    """Sliding-window rate limiter for API calls."""
    def __init__(self, max_calls: int = 20, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self._timestamps: list = []

    def check(self) -> bool:
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < self.period]
        if len(self._timestamps) >= self.max_calls:
            return False
        self._timestamps.append(now)
        return True


class Brain:
    def __init__(self, config, memory, executor, screen, kill_event):
        self.config      = config
        self.memory      = memory
        self.executor    = executor
        self.screen      = screen
        self.kill_event  = kill_event
        self.gui_cb      = None
        self.current_todo = []
        self._rate_limiter = _RateLimiter()
        self._init_client()
        self._init_modules()

    def _init_modules(self):
        try:
            from core.calendar_integration import CalendarManager
            self.calendar = CalendarManager(self.config)
        except Exception as e:
            logger.debug("[Brain] Calendar init: %s", e)
            self.calendar = None
        try:
            from core.tasks import TaskManager
            self.tasks = TaskManager(self.config)
        except Exception as e:
            logger.debug("[Brain] Tasks init: %s", e)
            self.tasks = None
        try:
            from core.email_manager import EmailManager
            self.email = EmailManager(self.config)
        except Exception as e:
            logger.debug("[Brain] Email init: %s", e)
            self.email = None
        try:
            from core.briefing import BriefingManager
            self.briefing = BriefingManager(self.config, self.calendar, self.tasks)
        except Exception as e:
            logger.debug("[Brain] Briefing init: %s", e)
            self.briefing = None
        try:
            from core.document_reader import DocumentReader
            self.doc_reader = DocumentReader()
        except Exception as e:
            logger.debug("[Brain] DocReader init: %s", e)
            self.doc_reader = None
        try:
            from core.smarthome import SmartHomeManager
            self.smarthome = SmartHomeManager(self.config)
        except Exception as e:
            logger.debug("[Brain] SmartHome init: %s", e)
            self.smarthome = None
        try:
            from core.youtube import YouTubeManager
            self.youtube = YouTubeManager()
        except Exception as e:
            logger.debug("[Brain] YouTube init: %s", e)
            self.youtube = None
        try:
            from core.finance import FinanceTracker
            self.finance = FinanceTracker(self.config)
        except Exception as e:
            logger.debug("[Brain] Finance init: %s", e)
            self.finance = None
        try:
            from core.research import ResearchAssistant
            self.research = ResearchAssistant(self.config)
        except Exception as e:
            logger.debug("[Brain] Research init: %s", e)
            self.research = None

    def _init_client(self):
        key = self.config.get("api_key", "")
        if self.config.get("api_provider", "anthropic") == "anthropic" and key:
            try:
                self.client = anthropic.Anthropic(
                    api_key=key,
                    timeout=httpx.Timeout(60.0, connect=10.0),  # Bug 1.3
                )
            except anthropic.AuthenticationError as e:
                logger.error("[Brain] Anthropic auth error: %s", e)
                self.client = None
            except (anthropic.APIConnectionError, httpx.HTTPError) as e:
                logger.error("[Brain] Client connection error: %s", e)
                self.client = None
        else:
            self.client = None

    def reload_config(self, new_config):
        self.config = new_config
        self._init_client()
        logger.info(
            "[Brain] Config neu. Provider: %s | Key: %s",
            new_config.get('api_provider'),
            'yes' if new_config.get('api_key') else 'no',
        )

    def set_gui_callback(self, cb): self.gui_cb = cb

    def _emit(self, event, data):
        if self.gui_cb:
            try:
                self.gui_cb(event, data)
            except (TypeError, AttributeError) as e:
                logger.warning("[Brain] emit-Fehler (callback issue): %s", e)
            except RuntimeError as e:
                logger.warning("[Brain] emit-Fehler (runtime): %s", e)
            except Exception as e:  # noqa: BLE001 – last-resort guard for GUI stability
                logger.error("[Brain] emit-Fehler (unexpected): %s", e)

    def _step(self, label, text):
        """Sendet einen Denkschritt ans GUI."""
        self._emit("thinking_step", {"label": label, "text": text})

    # ── Klassifizierung ───────────────────────────────────────────────────
    def classify(self, query):
        default = {"agent":"conversation","komplex":False,"sicherheitsrisiko":"keine",
                   "braucht_erlaubnis":False,"erlaubnis_grund":"","denkschritte":[],"zusammenfassung":query}
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        # v3.0: Klassifizierung provider-übergreifend. Früher lief sie NUR über den
        # Anthropic-Client (self.client) – auf NVIDIA/OpenAI/Gemini/Local fiel das
        # gesamte Agenten-Routing aus (alles wurde "conversation"). Jetzt nutzt
        # Anthropic weiterhin das günstige+schnelle Haiku-Classifier-Modell, alle
        # anderen Provider ihr Hauptmodell via decide_text().
        try:
            if provider == "anthropic" and self.client:
                classifier_model = self.config.get("classifier_model", "claude-haiku-4-5-20251001")  # Bug 1.16
                r = self.client.messages.create(
                    model=classifier_model, max_tokens=CLASSIFIER_MAX_TOKENS,  # Bug 1.21
                    messages=[{"role":"user","content": CLASSIFIER + query}])
                raw = r.content[0].text
            elif api_key or provider == "local":
                raw = self.decide_text(
                    "Du bist ein Anfragen-Klassifikator. Antworte AUSSCHLIESSLICH mit "
                    "einem einzigen validen JSON-Objekt – kein Markdown, kein Text drumherum.",
                    CLASSIFIER + query, max_tokens=CLASSIFIER_MAX_TOKENS)
            else:
                return default
            # Bug 1.5 / v3.0: robustes JSON-Parsing (toleriert Prosa um das JSON)
            parsed = _json_from_text(raw)
            if isinstance(parsed, dict) and parsed.get("agent"):
                merged = dict(default)   # fehlende Felder mit Defaults auffüllen
                merged.update(parsed)
                return merged
            logger.warning("[Brain] Classify: kein gültiges JSON | raw: %s", str(raw)[:200])
            return default
        except anthropic.APIError as e:
            logger.error("[Brain] Classify API-Fehler: %s", e)
            return default
        except (httpx.HTTPError, ConnectionError) as e:
            logger.error("[Brain] Classify Netzwerk-Fehler: %s", e)
            return default
        except Exception as e:  # noqa: BLE001 – Klassifizierung darf nie den Flow stoppen
            logger.error("[Brain] Classify unerwarteter Fehler: %s", e)
            return default

    def _build_system(self):
        system = SYSTEM_PROMPTS.get(self.config.get("redeart","normal"), SYSTEM_PROMPTS["normal"])
        anrede = self.config.get("anrede","")
        if anrede: system += f" Sprich den Nutzer mit '{anrede}' an."

        # Sprache
        lang = self.config.get("sprache","de-DE")
        if lang != "de-DE":
            system += f" Antworte ab jetzt auf {LANG_NAMES.get(lang,'Deutsch')}."

        # Standort – WICHTIG: verwenden, nicht fragen
        wohnort = self.config.get("wohnort","")
        if wohnort:
            system += (f"\n\nWICHTIG: Der Standort des Nutzers ist '{wohnort}'. "
                       f"Verwende diesen Standort IMMER direkt für Wetter, lokale Infos etc. "
                       f"Frage NIEMALS nach dem Standort – du kennst ihn bereits: {wohnort}.")

        ctx = self.memory.get_relevant_context("")
        if ctx: system += f"\n\nGespeicherte Infos über den Nutzer:\n{ctx}"
        scr = self.screen.get_description()
        if scr: system += f"\n\nBildschirm: {scr}"
        system += f"\n\nWenn du Code schreibst, nutze immer Markdown-Codeblöcke mit Sprachangabe (```python etc.). Formatiere Code sauber mit korrekter Einrückung. Teste die Logik mental bevor du antwortest."
        system += f"\n\nAktuelle Zeit: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        return system

    # ── Hauptverarbeitung ─────────────────────────────────────────────────
    def process(self, query, todo_mode=False):
        if self.kill_event.is_set(): return None

        if not self._rate_limiter.check():
            msg = "⚠️ Zu viele Anfragen – bitte kurz warten."
            self._emit("message", {"role":"jarvis","text": msg})
            return None

        provider = self.config.get("api_provider","anthropic")
        api_key  = self.config.get("api_key","")
        if not api_key and provider != "local":
            msg = "Bitte zuerst einen API-Key in den Einstellungen (⚙) eingeben."
            self._emit("message", {"role":"jarvis","text": msg})
            self._emit("needs_setup", {"reason":"Kein API-Key"})
            return None

        # YouTube-URL Auto-Detect
        if self.youtube and self.youtube.contains_youtube_url(query):
            self._step("YOUTUBE", "YouTube-Link erkannt...")
            result = self._agent_youtube(query)
            if result:
                self._emit("message", {"role":"jarvis","text": result})
                self.memory.add_to_history("user", query)
                self.memory.add_to_history("assistant", result)
                self._step("FERTIG", "Antwort gesendet.")
                self._emit("status", {"text":"✅ BEREIT"})
                if todo_mode:
                    return {"status":"ok","reply":result}
                return result

        # Schnelle Bildschirm-Erkennung (vor Klassifizierung)
        screen_kw = ["schau auf meinen bildschirm","screenshot","bildschirm anschauen",
                      "was siehst du","schau dir meinen bildschirm","hilf mir ich komme hier nicht weiter",
                      "was muss ich hier drücken","schau mal","was ist auf meinem bildschirm",
                      "kannst du meinen bildschirm sehen","zeig mir was ich sehe","screen"]
        if any(kw in query.lower() for kw in screen_kw):
            self._step("VISION", "Bildschirm-Anfrage erkannt – starte Countdown...")
            self._emit("screenshot_countdown", {"query": query})
            return None

        # ── v3.0 Speed: Konversations-Schnellpfad ────────────────────────────
        # Enthält die Anfrage KEIN Spezial-Agenten-Stichwort, ist es fast sicher
        # normale Konversation → den kompletten Classifier-Roundtrip überspringen
        # und direkt antworten. Spart bei jedem Chat-Turn einen ganzen LLM-Call.
        if _is_simple_conversation(query):
            cl = dict(default_classification(query))
            agent = "conversation"
            self._emit("agent_selected", {"agent": agent})
        else:
            # Klassifizierung
            self._emit("status", {"text":"🧠 Analysiere..."})
            self._step("ANALYSE", "Verstehe die Anfrage und wähle den passenden Agenten...")
            cl = self.classify(query)
            agent = cl.get("agent","conversation")
            self._emit("agent_selected", {"agent": agent})

        # Denkschritte ans GUI
        for s in cl.get("denkschritte", []):
            self._step("PLANUNG", s)

        if cl.get("sicherheitsrisiko") == "hoch":
            self._step("SICHERHEIT", "Risiko zu hoch – Aktion blockiert.")
            self._emit("safety_warning", {"level":"hoch","message":"Aktion blockiert – zu hohes Risiko."})
            if todo_mode: return {"status":"error","message":"Sicherheitsrisiko zu hoch."}
            return None

        if cl.get("braucht_erlaubnis") and not todo_mode:
            self._emit("permission_request", {"grund": cl.get("erlaubnis_grund","")})
            return None

        # Screen Vision: Countdown → Screenshot → Analyse
        if agent == "screen_vision":
            self._step("VISION", "Starte Bildschirm-Aufnahme mit Countdown...")
            self._emit("screenshot_countdown", {"query": query})
            return None

        # ── Spezial-Agenten mit eigener Logik ────────────────────────────
        special_result = self._handle_special_agent(agent, query)
        if special_result is not None:
            reply = special_result
            reply = self._process_code_in_reply(reply, self.config.get("api_provider","anthropic"), self.config.get("api_key",""), "", [])
            self._emit("message", {"role":"jarvis","text": reply})
            self.memory.add_to_history("user", query)
            self.memory.add_to_history("assistant", reply)
            self._step("FERTIG", "Antwort gesendet.")
            if any(kw in query.lower() for kw in ["merk dir","merke","denk daran","vergiss nicht"]):
                self._save_key_memory(query)
            self._emit("status", {"text":"✅ BEREIT"})
            if todo_mode:
                return {"status":"ok","reply":reply}
            return reply

        self._step("AUSFÜHRUNG", f"Agent [{agent}] generiert die Antwort...")

        system = self._build_system()
        hist = self.memory.get_conversation_history(8)
        messages = [{"role":m["role"],"content":m["content"]} for m in hist]
        messages.append({"role":"user","content": query})

        self._emit("status", {"text": f"⚡ [{agent.upper()}] antwortet..."})

        reply = ""
        try:
            reply = self._call(provider, api_key, system, messages)
        except anthropic.AuthenticationError:
            reply = "⚠️ API-Key ungültig (401). Bitte in den Einstellungen (⚙) neuen Key eingeben."
            self._emit("needs_setup", {"reason":"401"})
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response else "?"
            if code == 401: reply = "⚠️ API-Key ungültig. Einstellungen (⚙) öffnen."; self._emit("needs_setup",{"reason":"401"})
            elif code == 429: reply = "⚠️ Rate-Limit – kurz warten."
            else: reply = f"⚠️ HTTP-Fehler {code}."
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            reply = f"⚠️ Verbindungsfehler: {str(e)[:120]}"
            logger.error("[Brain] Verbindungsfehler: %s", e)
        except Exception as e:
            reply = f"⚠️ Fehler: {str(e)[:150]}"
            logger.error("[Brain] Fehler: %s", e)

        if reply:
            reply = self._process_code_in_reply(reply, provider, api_key, system, messages)
            self._emit("message", {"role":"jarvis","text": reply})
            self.memory.add_to_history("user", query)
            self.memory.add_to_history("assistant", reply)
            self._step("FERTIG", "Antwort gesendet.")

        # Memory: nur Key=Value extrahieren
        if any(kw in query.lower() for kw in ["merk dir","merke","denk daran","vergiss nicht"]):
            self._save_key_memory(query)

        self._emit("status", {"text":"✅ BEREIT"})
        if todo_mode:
            return {"status":"ok","reply":reply}
        return reply

    # ── Spezial-Agenten Handler ─────────────────────────────────────────
    def _handle_special_agent(self, agent, query):
        """Verarbeitet Agenten mit eigener Logik. Gibt Text zurück oder None für Standard-Verarbeitung."""

        if agent == "briefing":
            return self._agent_briefing(query)
        elif agent == "youtube":
            return self._agent_youtube(query)
        elif agent == "finance":
            return self._agent_finance(query)
        elif agent == "research":
            return self._agent_research(query)
        elif agent == "calendar":
            return self._agent_calendar(query)
        elif agent == "tasks":
            return self._agent_tasks(query)
        elif agent == "email":
            return self._agent_email(query)
        elif agent == "document":
            return self._agent_document(query)
        elif agent == "smarthome":
            return self._agent_smarthome(query)

        # social_media, decision → Standard-Verarbeitung mit angepasstem System-Prompt
        if agent == "social_media":
            return self._agent_social_media(query)
        if agent == "decision":
            return self._agent_decision(query)

        return None

    def _agent_briefing(self, query):
        self._step("BRIEFING", "Sammle Daten für das Briefing...")
        if not self.briefing:
            return "Briefing-Modul nicht verfügbar."
        sections = self.briefing.generate_briefing()
        text = self.briefing.format_briefing(sections)
        # Zur sprachlichen Aufbereitung ans Modell senden
        return self._enrich_with_model(text, "Formuliere dieses Briefing natürlich und ansprechend auf Deutsch. Behalte alle Informationen bei:")

    def _agent_youtube(self, query):
        self._step("YOUTUBE", "Suche YouTube-Transkript...")
        if not self.youtube or not self.youtube.is_available:
            return "YouTube-Modul nicht verfügbar. Bitte `pip install youtube-transcript-api` installieren."
        video_id = self.youtube.extract_video_id(query)
        if not video_id:
            return "Kein YouTube-Link in der Nachricht gefunden."
        prompt = self.youtube.get_video_summary_prompt(video_id)
        if not prompt:
            return "Für dieses Video ist kein Transkript verfügbar."
        self._step("YOUTUBE", "Erstelle Zusammenfassung...")
        return self._enrich_with_model(prompt, None)

    def _agent_finance(self, query):
        self._step("FINANZEN", "Verarbeite Finanz-Anfrage...")
        if not self.finance:
            return "Finanz-Modul nicht verfügbar."
        q = query.lower()
        if any(kw in q for kw in ["ausgegeben", "bezahlt", "gekauft", "gekostet"]):
            import re
            amount_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:€|euro|eur)', q)
            if amount_match:
                amount = float(amount_match.group(1).replace(",", "."))
                self.finance.add_expense(amount, "", query)
                return f"Ausgabe von {amount:.2f}€ gespeichert.\n\n{self.finance.format_summary_text(30)}"
        if any(kw in q for kw in ["budget", "setze", "limit"]):
            return "Bitte nenne die Kategorie und den Betrag, z.B.: 'Setze Budget für Lebensmittel auf 300 Euro'."
        if any(kw in q for kw in ["übersicht", "zusammenfassung", "ausgaben", "wie viel"]):
            days = 7 if "woche" in q else 30
            return self.finance.format_summary_text(days)
        return self.finance.format_summary_text(30)

    def _agent_research(self, query):
        self._step("RECHERCHE", "Sammle aktuelle Artikel...")
        if not self.research:
            return "Research-Modul nicht verfügbar."
        q = query.lower()
        import re
        detail_match = re.search(r'(?:mehr zu|details zu|artikel)\s*(\d+)', q)
        if detail_match:
            idx = int(detail_match.group(1))
            article = self.research.get_article_detail(idx)
            if article:
                return f"**{article['title']}**\n\n{article.get('summary', '')}\n\nLink: {article.get('link', '')}"
            return f"Artikel {idx} nicht gefunden."
        articles = self.research.fetch_articles()
        text = self.research.format_research_text(articles)
        return f"**Research-Briefing:**\n\n{text}"

    def _agent_calendar(self, query):
        self._step("KALENDER", "Prüfe Kalender...")
        if not self.calendar or not self.calendar.is_configured:
            return ("Google Calendar nicht konfiguriert. Bitte `google_credentials.json` "
                    "in `~/.jarvis/` ablegen und JARVIS neu starten.")
        q = query.lower()
        if any(kw in q for kw in ["erstelle", "anlegen", "neuer termin", "termin für"]):
            return self._calendar_create(query)
        days = 1
        if "woche" in q:
            days = 7
        elif "morgen" in q:
            days = 2
        events = self.calendar.get_events(days)
        text = self.calendar.format_events_text(events)
        period = "morgen" if "morgen" in q else ("diese Woche" if days == 7 else "heute")
        return f"**Termine {period}:**\n\n{text}"

    def _calendar_create(self, query):
        from core.calendar_integration import parse_datetime_natural
        # Ans Modell senden, um Titel und Zeit zu extrahieren
        extract_prompt = (
            f"Extrahiere aus dieser Anfrage den Termintitel und die Uhrzeit/Datum. "
            f"Antworte NUR mit JSON: {{\"title\": \"...\", \"datetime\": \"YYYY-MM-DDTHH:MM\", \"duration\": 60}}\n"
            f"Anfrage: {query}"
        )
        try:
            result = self._call_sync(
                self.config.get("api_provider", "anthropic"),
                self.config.get("api_key", ""),
                "Du bist ein JSON-Extractor. Antworte NUR mit validem JSON.",
                [{"role": "user", "content": extract_prompt}]
            )
            if result:
                import json as _json
                raw = result.strip()
                if "```" in raw:
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                data = _json.loads(raw.strip())
                dt = datetime.fromisoformat(data["title"] if "T" in data.get("title", "") else data["datetime"])
                event = self.calendar.create_event(data["title"], dt, data.get("duration", 60))
                if event:
                    return f"Termin erstellt: **{event['title']}** am {event['start']}"
        except Exception as e:
            logger.debug("[Brain] Calendar create parse error: %s", e)

        dt = parse_datetime_natural(query)
        if dt:
            event = self.calendar.create_event(query[:50], dt)
            if event:
                return f"Termin erstellt: **{event['title']}** am {event['start']}"
        return "Konnte den Termin nicht erstellen. Bitte nenne Titel und Datum/Uhrzeit."

    def _agent_tasks(self, query):
        self._step("AUFGABEN", "Prüfe Tasks...")
        if not self.tasks or not self.tasks.is_configured:
            return "Aufgabenverwaltung nicht konfiguriert. Bitte Todoist/Notion API-Key in den Einstellungen eintragen."
        q = query.lower()
        if any(kw in q for kw in ["füge hinzu", "neue aufgabe", "hinzufügen", "erstelle task"]):
            content = query
            for prefix in ["füge zur todoist hinzu:", "füge hinzu:", "neue aufgabe:", "erstelle task:"]:
                if prefix in q:
                    content = query[q.index(prefix) + len(prefix):].strip()
                    break
            result = self.tasks.add_task(content)
            if result:
                return f"Aufgabe erstellt: **{result['content']}** [{result['source']}]"
            return "Konnte Aufgabe nicht erstellen."
        task_list = self.tasks.get_all_tasks()
        return f"**Offene Aufgaben ({self.tasks.get_provider_name()}):**\n\n{self.tasks.format_tasks_text(task_list)}"

    def _agent_email(self, query):
        self._step("E-MAIL", "Prüfe E-Mails...")
        if not self.email or not self.email.is_configured:
            return "E-Mail nicht konfiguriert. Bitte IMAP-Server, E-Mail-Adresse und App-Passwort in den Einstellungen eintragen."
        q = query.lower()
        if any(kw in q for kw in ["sende", "schicke", "absenden", "bestätige"]):
            if self.email.get_pending_draft():
                if self.email.send_pending_draft():
                    return "E-Mail wurde gesendet."
                return "Fehler beim Senden der E-Mail."
        if any(kw in q for kw in ["zusammenfas", "ungelesen", "posteingang", "mails"]):
            emails = self.email.get_unread(5)
            text = self.email.format_emails_text(emails)
            if not emails:
                return text
            return self._enrich_with_model(
                f"Fasse diese ungelesenen E-Mails zusammen:\n\n{text}",
                "Erstelle eine kompakte Zusammenfassung der E-Mails auf Deutsch:"
            )
        return "Was möchtest du mit deinen E-Mails machen? (zusammenfassen, Antwort schreiben...)"

    def _agent_document(self, query):
        self._step("DOKUMENT", "Analysiere Dokument...")
        if not self.doc_reader:
            return "Dokumenten-Modul nicht verfügbar."
        import re
        path_match = re.search(r'[A-Za-z]:[/\\][^\s]+|/[^\s]+|~[/\\][^\s]+', query)
        if not path_match:
            return "Bitte nenne den Dateipfad, z.B.: 'Fasse C:\\Dokumente\\report.pdf zusammen'"
        filepath = path_match.group(0)
        text = self.doc_reader.read(filepath)
        if not text:
            return f"Konnte '{filepath}' nicht lesen. Unterstützte Formate: {', '.join(self.doc_reader.get_supported_extensions())}"
        from core.document_reader import DocumentReader
        chunks = DocumentReader.chunk_text(text)
        if len(chunks) == 1:
            return self._enrich_with_model(
                f"Fasse dieses Dokument zusammen:\n\n{chunks[0][:6000]}",
                "Erstelle eine strukturierte Zusammenfassung auf Deutsch:"
            )
        chunk_text = "\n\n---\n\n".join(f"Abschnitt {i+1}:\n{c[:2000]}" for i, c in enumerate(chunks[:5]))
        return self._enrich_with_model(
            f"Fasse dieses Dokument zusammen ({len(chunks)} Abschnitte):\n\n{chunk_text}",
            "Erstelle eine Gesamtzusammenfassung auf Deutsch:"
        )

    def _agent_smarthome(self, query):
        self._step("SMART HOME", "Steuere Geräte...")
        if not self.smarthome or not self.smarthome.is_configured:
            return self.smarthome.get_status_text() if self.smarthome else "Smart-Home-Modul nicht verfügbar."
        # Einfache Keyword-Erkennung, komplexere via Modell
        q = query.lower()
        if any(kw in q for kw in ["licht an", "licht ein"]):
            lights = self.smarthome.get_entities("light")
            if lights:
                self.smarthome.light_on(lights[0]["entity_id"])
                return f"Licht eingeschaltet: {lights[0]['name']}"
        elif any(kw in q for kw in ["licht aus"]):
            lights = self.smarthome.get_entities("light")
            if lights:
                self.smarthome.light_off(lights[0]["entity_id"])
                return f"Licht ausgeschaltet: {lights[0]['name']}"
        elif "temperatur" in q:
            import re
            temp_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:°|grad)', q)
            if temp_match:
                temp = float(temp_match.group(1))
                climate = self.smarthome.get_entities("climate")
                if climate:
                    self.smarthome.set_temperature(climate[0]["entity_id"], temp)
                    return f"Temperatur auf {temp}°C gesetzt: {climate[0]['name']}"
        elif any(kw in q for kw in ["musik", "spotify", "spiele"]):
            media = self.smarthome.get_entities("media_player")
            if media:
                self.smarthome.play_media(media[0]["entity_id"])
                return f"Wiedergabe gestartet: {media[0]['name']}"
        return self.smarthome.get_status_text()

    def _agent_social_media(self, query):
        q = query.lower()
        if "linkedin" in q:
            platform_prompt = ("Erstelle einen professionellen LinkedIn-Post zum genannten Thema. "
                              "150-300 Wörter, 3-5 relevante Hashtags, Emojis sparsam. "
                              "Professioneller, inspirierender Ton.")
        elif "twitter" in q or "thread" in q:
            platform_prompt = ("Erstelle einen Twitter/X-Thread zum genannten Thema. "
                              "Max. 280 Zeichen pro Tweet. Thread mit 5-8 Tweets, nummeriert (1/N). "
                              "Prägnant und engaging.")
        elif "newsletter" in q:
            platform_prompt = ("Erstelle einen Newsletter-Entwurf zum genannten Thema. "
                              "Strukturiert mit Intro, Hauptteil, Call-to-Action. Max. 500 Wörter.")
        else:
            platform_prompt = ("Erstelle einen Social-Media-Post zum genannten Thema. "
                              "Passend für die Plattform, professioneller Ton.")
        return self._enrich_with_model(query, platform_prompt)

    def _agent_decision(self, query):
        ctx = self.memory.get_relevant_context("")
        decision_prompt = (
            "Hilf bei dieser Entscheidung mit einer strukturierten Pro/Kontra-Analyse. "
            "Format:\n"
            "1. Optionen identifizieren\n"
            "2. Pro/Kontra für jede Option (Tabelle oder Liste)\n"
            "3. Klare Empfehlung mit Begründung\n"
        )
        if ctx:
            decision_prompt += f"\nKontext über den Nutzer:\n{ctx}"
        return self._enrich_with_model(query, decision_prompt)

    def _enrich_with_model(self, content, instruction):
        """Sendet Content ans Modell zur sprachlichen Aufbereitung."""
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        if not api_key and provider != "local":
            return content

        system = instruction or self._build_system()
        messages = [{"role": "user", "content": content}]

        try:
            return self._call(provider, api_key, system, messages)
        except Exception as e:
            logger.warning("[Brain] Enrich fehlgeschlagen: %s", e)
            return content

    # ── Memory: Key=Value Extraktion ──────────────────────────────────────
    def _save_key_memory(self, query):
        # v3.0: provider-übergreifend. Früher nur Anthropic (self.client) – auf
        # NVIDIA/OpenAI/Gemini/Local wurde "merk dir ..." komplett ignoriert.
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        kv = None
        try:
            if provider == "anthropic" and self.client:
                classifier_model = self.config.get("classifier_model", "claude-haiku-4-5-20251001")  # Bug 1.16
                r = self.client.messages.create(
                    model=classifier_model, max_tokens=MEMORY_EXTRACT_MAX_TOKENS,  # Bug 1.21
                    messages=[{"role":"user","content": MEMORY_EXTRACT + query}])
                kv = r.content[0].text.strip().split("\n")[0]
            elif api_key or provider == "local":
                raw = self.decide_text(
                    "Du extrahierst die wichtigste zu merkende Information als kurzes "
                    "Key=Value-Paar. Antworte NUR mit dem Key=Value-Paar, nichts anderes.",
                    MEMORY_EXTRACT + query, max_tokens=MEMORY_EXTRACT_MAX_TOKENS)
                kv = (raw or "").strip().split("\n")[0] if raw else None
        except anthropic.APIError as e:
            logger.error("[Brain] Memory-Extract API-Fehler: %s", e)
        except (httpx.HTTPError, ConnectionError) as e:
            logger.error("[Brain] Memory-Extract Netzwerk-Fehler: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.error("[Brain] Memory-Extract Fehler: %s", e)
        if kv and "=" in kv:
            self.memory.save_memory_kv(kv)
            self._emit("memory_saved", {"query": kv})
            logger.info("[Memory] Key-Info gespeichert: %s", kv)
        else:
            logger.debug("[Memory] Konnte kein Key=Value extrahieren: %s", query[:60])

    # ── Screenshot-Analyse ─────────────────────────────────────────────
    def analyze_screenshot(self, screenshot_b64: str, query: str = "") -> str:
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        if not api_key and provider != "local":
            return "Kein API-Key verfügbar. Bitte in den Einstellungen eingeben."

        self._step("VISION", "Analysiere den Screenshot...")

        anrede = self.config.get("anrede", "")
        anrede_str = f" Sprich den Nutzer mit '{anrede}' an." if anrede else ""
        system = (
            f"Du bist JARVIS, ein KI-Assistent der den Bildschirm des Nutzers sieht.{anrede_str} "
            "Beschreibe GENAU was du siehst und gib spezifische Anweisungen mit Positionsangaben "
            "(z.B. 'oben links', 'in der Mitte', 'unten rechts'). "
            "Sei präzise: nenne Button-Texte, Menüpunkte, Fenster-Titel etc."
        )
        user_text = query if query else "Was siehst du auf meinem Bildschirm? Beschreibe was du siehst."

        try:
            if provider == "anthropic" and self.client:
                anthropic_model = self.config.get("anthropic_model", "claude-sonnet-4-6")  # Bug 1.16
                r = self.client.messages.create(
                    model=anthropic_model, max_tokens=1024, system=system,
                    messages=[{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": screenshot_b64}},
                        {"type": "text", "text": user_text}
                    ]}])
                reply = r.content[0].text

            elif provider in ("openai", "nvidia", "mistral"):
                base_urls = {
                    "openai": "https://api.openai.com/v1",
                    "nvidia": "https://integrate.api.nvidia.com/v1",
                    "mistral": "https://api.mistral.ai/v1",
                }
                vision_models = {
                    "openai": "gpt-4o-mini",
                    "nvidia": "meta/llama-3.2-90b-vision-instruct",
                    "mistral": "pixtral-large-latest",
                }
                img_url = f"data:image/jpeg;base64,{screenshot_b64}"
                r = requests.post(
                    f"{base_urls[provider]}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": vision_models[provider], "max_tokens": 1024,
                          "messages": [
                              {"role": "system", "content": system},
                              {"role": "user", "content": [
                                  {"type": "image_url", "image_url": {"url": img_url}},
                                  {"type": "text", "text": user_text}
                              ]}
                          ]}, timeout=90)
                r.raise_for_status()
                data = r.json()
                # Bug 1.4: validate response structure
                if not data.get("choices") or not isinstance(data["choices"], list) or len(data["choices"]) == 0:
                    return "Fehler: API-Antwort enthält keine 'choices'."
                choice = data["choices"][0]
                if not isinstance(choice, dict) or "message" not in choice or "content" not in choice.get("message", {}):
                    return "Fehler: Unerwartetes Format in API-Antwort."
                reply = choice["message"]["content"]

            elif provider == "gemini":
                prompt = system + "\n\n" + user_text
                r = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
                    headers={"x-goog-api-key": api_key},
                    json={"contents": [{"parts": [
                        {"inline_data": {"mime_type": "image/jpeg", "data": screenshot_b64}},
                        {"text": prompt}
                    ]}]}, timeout=90)
                r.raise_for_status()
                data = r.json()
                # Bug 1.4: validate response structure
                if not data.get("candidates") or not isinstance(data["candidates"], list) or len(data["candidates"]) == 0:
                    return "Fehler: Gemini-Antwort enthält keine 'candidates'."
                try:
                    reply = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, TypeError) as e:
                    logger.error("[Brain] Gemini Vision response parse error: %s", e)
                    return "Fehler: Unerwartetes Gemini-Antwortformat."

            elif provider == "local":
                url = self.config.get("local_url", "http://localhost:11434") + "/api/chat"
                model = self.config.get("local_model", "llama3")
                r = requests.post(url, json={"model": model, "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_text, "images": [screenshot_b64]}
                    ]}, timeout=120)
                r.raise_for_status()
                data = r.json()
                # Bug 1.4: validate response structure
                if not isinstance(data, dict) or "message" not in data or "content" not in data.get("message", {}):
                    return "Fehler: Ollama-Antwort enthält kein 'message.content'."
                reply = data["message"]["content"]
            else:
                reply = "Unbekannter Provider für Bildschirm-Analyse."

            self._step("FERTIG", "Screenshot analysiert.")
            self.memory.add_to_history("user", f"[Screenshot] {user_text}")
            self.memory.add_to_history("assistant", reply)
            return reply
        except anthropic.APIError as e:
            logger.error("[Brain] Vision API-Fehler: %s", e)
            return f"API-Fehler bei der Bildschirm-Analyse: {str(e)[:150]}"
        except requests.exceptions.Timeout:
            logger.error("[Brain] Vision Timeout")
            return "Fehler: Zeitüberschreitung bei der Bildschirm-Analyse."
        except requests.exceptions.ConnectionError as e:
            logger.error("[Brain] Vision Verbindungsfehler: %s", e)
            return f"Verbindungsfehler bei der Bildschirm-Analyse: {str(e)[:150]}"
        except Exception as e:
            logger.error("[Brain] Vision-Fehler: %s", e)
            return f"Fehler bei der Bildschirm-Analyse: {str(e)[:150]}"

    # ── Vision-Entscheidung (Copilot) ──────────────────────────────────────
    def vision_decide(self, screenshot_b64: str, system: str, user_text: str,
                      max_tokens: int = 700) -> str:
        """Schlanker Multi-Provider Vision-Call für den Copilot.

        Gibt den ROHTEXT der Modell-Antwort zurück (typischerweise JSON).
        Keine Memory-/Step-Seiteneffekte – der Copilot parst selbst.
        Wirft KEINE Exceptions nach außen für Netz-/API-Fehler; liefert dann
        einen leeren String, damit der Copilot-Loop sauber reagieren kann.
        """
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        if not api_key and provider != "local":
            return ""

        try:
            if provider == "anthropic":
                if not self.client:
                    self._init_client()
                if not self.client:
                    return ""
                model = self.config.get("anthropic_model", "claude-sonnet-4-6")
                r = self.client.messages.create(
                    model=model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64",
                            "media_type": "image/jpeg", "data": screenshot_b64}},
                        {"type": "text", "text": user_text},
                    ]}])
                return r.content[0].text

            elif provider in ("openai", "nvidia", "mistral"):
                base_urls = {
                    "openai": "https://api.openai.com/v1",
                    "nvidia": "https://integrate.api.nvidia.com/v1",
                    "mistral": "https://api.mistral.ai/v1",
                }
                vision_models = {
                    "openai": self.config.get("openai_model", "gpt-4o-mini"),
                    # v2.9: das alte llama-3.2-90b-vision verweigert PC-Steuerung und
                    # liefert kein JSON. llama-4-maverick ist multimodal, robust und
                    # befolgt die JSON-Vorgabe. Über nvidia_vision_model übersteuerbar.
                    "nvidia": self.config.get("nvidia_vision_model",
                                              "meta/llama-4-maverick-17b-128e-instruct"),
                    "mistral": "pixtral-large-latest",
                }
                img_url = f"data:image/jpeg;base64,{screenshot_b64}"
                r = requests.post(
                    f"{base_urls[provider]}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": vision_models[provider], "max_tokens": max_tokens,
                          "messages": [
                              {"role": "system", "content": system},
                              {"role": "user", "content": [
                                  {"type": "image_url", "image_url": {"url": img_url}},
                                  {"type": "text", "text": user_text},
                              ]}]}, timeout=90)
                r.raise_for_status()
                data = r.json()
                if not data.get("choices"):
                    return ""
                return data["choices"][0].get("message", {}).get("content", "") or ""

            elif provider == "gemini":
                prompt = system + "\n\n" + user_text
                r = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
                    headers={"x-goog-api-key": api_key},
                    json={"contents": [{"parts": [
                        {"inline_data": {"mime_type": "image/jpeg", "data": screenshot_b64}},
                        {"text": prompt},
                    ]}]}, timeout=90)
                r.raise_for_status()
                data = r.json()
                if not data.get("candidates"):
                    return ""
                return data["candidates"][0]["content"]["parts"][0]["text"]

            elif provider == "local":
                url = self.config.get("local_url", "http://localhost:11434") + "/api/chat"
                model = self.config.get("local_model", "llama3")
                r = requests.post(url, json={"model": model, "stream": False, "format": "json",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_text, "images": [screenshot_b64]},
                    ]}, timeout=120)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    return ""
                return data.get("message", {}).get("content", "") or ""

            return ""
        except anthropic.APIError as e:
            logger.error("[Brain] vision_decide API-Fehler: %s", e)
            return ""
        except (requests.exceptions.RequestException, httpx.HTTPError) as e:
            logger.error("[Brain] vision_decide Netzwerk-Fehler: %s", e)
            return ""
        except (KeyError, IndexError, TypeError) as e:
            logger.error("[Brain] vision_decide Parse-Fehler: %s", e)
            return ""

    def decide_text(self, system: str, user_text: str,
                    max_tokens: int = 300) -> str:
        """v2.9: Schlanker Multi-Provider TEXT-Call (ohne Bild) für den Copilot.

        Wird z.B. genutzt um aus einer Prozess-/App-Liste den passenden Eintrag
        wählen zu lassen (Spec 1a) oder für die Selbstreflexion ohne Screenshot.
        Gibt den ROHTEXT zurück; bei Fehlern einen leeren String (nie Exception).
        """
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        if not api_key and provider != "local":
            return ""
        try:
            messages = [{"role": "user", "content": user_text}]
            if provider == "anthropic":
                if not self.client:
                    self._init_client()
                if not self.client:
                    return ""
                model = self.config.get("anthropic_model", "claude-sonnet-4-6")
                r = self.client.messages.create(
                    model=model, max_tokens=max_tokens, system=system,
                    messages=messages)
                return r.content[0].text

            elif provider in ("openai", "nvidia", "mistral"):
                base_urls = {
                    "openai": "https://api.openai.com/v1",
                    "nvidia": "https://integrate.api.nvidia.com/v1",
                    "mistral": "https://api.mistral.ai/v1",
                }
                models = {
                    "openai": self.config.get("openai_model", "gpt-4o-mini"),
                    "nvidia": self.config.get("nvidia_model", "meta/llama-3.1-70b-instruct"),
                    "mistral": "mistral-large-latest",
                }
                r = requests.post(
                    f"{base_urls[provider]}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={"model": models[provider], "max_tokens": max_tokens,
                          "messages": [{"role": "system", "content": system}] + messages},
                    timeout=60)
                r.raise_for_status()
                data = r.json()
                if not data.get("choices"):
                    return ""
                return data["choices"][0].get("message", {}).get("content", "") or ""

            elif provider == "gemini":
                r = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
                    headers={"x-goog-api-key": api_key},
                    json={"contents": [{"parts": [{"text": system + "\n\n" + user_text}]}]},
                    timeout=60)
                r.raise_for_status()
                data = r.json()
                if not data.get("candidates"):
                    return ""
                return data["candidates"][0]["content"]["parts"][0]["text"]

            elif provider == "local":
                url = self.config.get("local_url", "http://localhost:11434") + "/api/chat"
                model = self.config.get("local_model", "llama3")
                r = requests.post(url, json={"model": model, "stream": False,
                    "messages": [{"role": "system", "content": system}] + messages},
                    timeout=120)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    return ""
                return data.get("message", {}).get("content", "") or ""

            return ""
        except anthropic.APIError as e:
            logger.error("[Brain] decide_text API-Fehler: %s", e)
            return ""
        except (requests.exceptions.RequestException, httpx.HTTPError) as e:
            logger.error("[Brain] decide_text Netzwerk-Fehler: %s", e)
            return ""
        except (KeyError, IndexError, TypeError) as e:
            logger.error("[Brain] decide_text Parse-Fehler: %s", e)
            return ""

    # ── To-Do Ausführung ──────────────────────────────────────────────────
    def run_todo_item(self, item_text):
        """Führt eine einzelne To-Do Aufgabe aus. Gibt dict zurück."""
        if self.kill_event.is_set():
            return {"status":"error","message":"Kill-Switch aktiv."}
        result = self.process(item_text, todo_mode=True)
        return result or {"status":"ok","reply":""}

    # ── Provider-Router ───────────────────────────────────────────────────
    def _call(self, provider, api_key, system, messages):
        if provider == "anthropic":
            return self._anthropic(system, messages)
        elif provider == "openai":
            return self._openai_compat("https://api.openai.com/v1", api_key, self.config.get("openai_model","gpt-4o-mini"), system, messages)
        elif provider == "nvidia":
            return self._openai_compat("https://integrate.api.nvidia.com/v1", api_key, self.config.get("nvidia_model","meta/llama-3.1-70b-instruct"), system, messages)
        elif provider == "mistral":
            return self._openai_compat("https://api.mistral.ai/v1", api_key, "mistral-large-latest", system, messages)
        elif provider == "gemini":
            return self._gemini(api_key, system, messages)
        elif provider == "local":
            return self._ollama(system, messages)
        return "Unbekannter Provider."

    def _anthropic(self, system, messages):
        if not self.client: self._init_client()
        if not self.client: return "Anthropic-Client nicht initialisiert."
        anthropic_model = self.config.get("anthropic_model", "claude-sonnet-4-6")  # Bug 1.16
        full = ""
        with self.client.messages.stream(
            model=anthropic_model, max_tokens=MAX_RESPONSE_TOKENS,  # Bug 1.21
            system=system, messages=messages) as stream:
            for chunk in stream.text_stream:
                if self.kill_event.is_set(): break
                full += chunk
                self._emit("stream_chunk", {"text": chunk})
        return full

    def _openai_compat(self, base_url, api_key, model, system, messages):
        msgs = [{"role":"system","content":system}] + messages
        r = requests.post(f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type":"application/json"},
            json={"model":model,"max_tokens":MAX_RESPONSE_TOKENS,"messages":msgs}, timeout=60)  # Bug 1.21
        r.raise_for_status()
        data = r.json()
        # Bug 1.4: validate response structure
        if not data.get("choices") or not isinstance(data["choices"], list) or len(data["choices"]) == 0:
            return "Fehler: API-Antwort enthält keine gültigen 'choices'."
        choice = data["choices"][0]
        if not isinstance(choice, dict) or "message" not in choice:
            return "Fehler: Unerwartetes Antwortformat (kein 'message' in choice)."
        msg = choice["message"]
        if not isinstance(msg, dict) or "content" not in msg:
            return "Fehler: Unerwartetes Antwortformat (kein 'content' in message)."
        return msg["content"]

    def _gemini(self, api_key, system, messages):
        prompt = system + "\n\n" + "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        r = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
            headers={"x-goog-api-key": api_key},
            json={"contents":[{"parts":[{"text":prompt}]}]}, timeout=60)
        r.raise_for_status()
        data = r.json()
        # Bug 1.4: validate response structure
        if not data.get("candidates") or not isinstance(data["candidates"], list) or len(data["candidates"]) == 0:
            return "Fehler: Gemini-Antwort enthält keine 'candidates'."
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            logger.error("[Brain] Gemini response parse error: %s", e)
            return "Fehler: Unerwartetes Gemini-Antwortformat."

    # ── Code-Block Processing ────────────────────────────────────────────
    def _process_code_in_reply(self, reply, provider, api_key, system, messages):
        """Test code blocks, fix syntax errors, format before display."""
        from core.code_processor import extract_code_blocks, test_code, format_code

        blocks = extract_code_blocks(reply)
        if not blocks:
            return reply

        result = reply
        for block in reversed(blocks):
            lang = block["language"]
            code = block["code"]
            if not lang:
                continue

            success, error_msg = test_code(lang, code)
            if not success:
                fixed = self._fix_code_block(lang, code, error_msg, provider, api_key)
                if fixed:
                    code = fixed
                else:
                    continue

            formatted = format_code(lang, code)
            new_block = f"```{block['language']}\n{formatted}```"
            result = result[:block["start"]] + new_block + result[block["end"]:]

        return result

    def _fix_code_block(self, lang, code, error_msg, provider, api_key):
        """Try to fix a broken code block via API. Max 3 attempts."""
        from core.code_processor import extract_code_blocks, test_code

        for attempt in range(3):
            self._step("CODE-FIX", f"Syntaxfehler in {lang} erkannt – korrigiere (Versuch {attempt + 1}/3)...")

            fix_prompt = (
                f"Dieser {lang}-Code hat einen Syntaxfehler:\n"
                f"```{lang}\n{code}```\n"
                f"Fehler: {error_msg}\n"
                f"Gib NUR den korrigierten Code zurück in einem ```{lang} Code-Block."
            )

            try:
                fix_reply = self._call_sync(
                    provider, api_key,
                    "Du bist ein Code-Korrektur-Assistent. Gib NUR korrigierten Code zurück.",
                    [{"role": "user", "content": fix_prompt}]
                )
            except Exception as e:
                logger.warning("[Brain] Code-Fix Versuch %d fehlgeschlagen: %s", attempt + 1, e)
                break

            if not fix_reply:
                break

            fix_blocks = extract_code_blocks(fix_reply)
            if not fix_blocks:
                continue

            fixed_code = fix_blocks[0]["code"]
            success, new_error = test_code(lang, fixed_code)
            if success:
                return fixed_code

            code = fixed_code
            error_msg = new_error

        return None

    def _call_sync(self, provider, api_key, system, messages):
        """Non-streaming API call for code correction retries."""
        if provider == "anthropic":
            if not self.client:
                self._init_client()
            if not self.client:
                return None
            anthropic_model = self.config.get("anthropic_model", "claude-sonnet-4-6")
            r = self.client.messages.create(
                model=anthropic_model, max_tokens=MAX_RESPONSE_TOKENS,
                system=system, messages=messages
            )
            return r.content[0].text
        return self._call(provider, api_key, system, messages)

    def _ollama(self, system, messages):
        url = self.config.get("local_url","http://localhost:11434") + "/api/chat"
        model = self.config.get("local_model","llama3")
        msgs = [{"role":"system","content":system}] + messages
        r = requests.post(url, json={"model":model,"stream":False,"messages":msgs}, timeout=120)
        r.raise_for_status()
        data = r.json()
        # Bug 1.4: validate response structure
        if not isinstance(data, dict) or "message" not in data:
            return "Fehler: Ollama-Antwort enthält kein 'message'-Feld."
        msg = data["message"]
        if not isinstance(msg, dict) or "content" not in msg:
            return "Fehler: Ollama-Antwort enthält kein 'content' in 'message'."
        return msg["content"]
