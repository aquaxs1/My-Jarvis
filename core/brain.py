"""
JARVIS Brain v2.8 – synchron
- Thinking steps (thinking_step events)
- The location is used, never asked for
- Memory: extract Key=Value only
- To-do execution
- v2.0: Specific exceptions, timeouts, response validation,
        configurable models, constants
"""
import json, logging, time, requests, anthropic

# httpx supplies the client timeout and the transport errors caught below. It
# used to arrive as a dependency of anthropic; anthropic 1.x depends on httpx2
# (the same library, 2.x line) instead, so a plain `import httpx` started
# failing at startup on a fresh install. Either name works here, and
# requirements.txt now asks for httpx explicitly rather than relying on
# whatever anthropic happens to pull in.
try:
    import httpx
except ImportError:  # anthropic >= 1.0 ships httpx2
    import httpx2 as httpx
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Constants (Bug 1.21) ─────────────────────────────────────────────────
MAX_RESPONSE_TOKENS = 2048
CLASSIFIER_MAX_TOKENS = 400
MEMORY_EXTRACT_MAX_TOKENS = 60

SYSTEM_PROMPTS = {
    "professional": "You are My Jarvis, a highly capable AI assistant. Answer precisely in English. Technical terms are welcome.",
    "normal":        "You are My Jarvis, a friendly AI assistant. Answer clearly and understandably in English.",
    "casual":    "You are My Jarvis, a laid-back AI assistant. Use casual slang. Answer in English.",
}

# Language display names – could be externalized to a config file or JSON resource (Bug 1.17)
LANG_NAMES = {
    "de-DE":"German","en-US":"English","fr-FR":"French",
    "es-ES":"Spanish","it-IT":"Italian","tr-TR":"Turkish",
}

CLASSIFIER = """Analyse the user request. Answer with JSON ONLY (no backticks, no markdown):
{
  "agent": "conversation|computer_control|web_search|coding|analysis|planning|memory|system|screen_vision|calendar|tasks|email|briefing|document|smarthome|youtube|social_media|decision|finance|research",
  "komplex": false,
  "sicherheitsrisiko": "none",
  "braucht_erlaubnis": false,
  "erlaubnis_grund": "",
  "denkschritte": ["short step 1", "short step 2"],
  "zusammenfassung": "short description"
}
Agent routing:
- calendar: appointments, calendar, "what do I have tomorrow", create an event
- tasks: tasks, to-dos, Todoist, Notion tasks
- email: email, inbox, writing a reply
- briefing: briefing, daily overview, "give me my briefing"
- document: read a PDF, analyse a document, summarise a file
- smarthome: lights, temperature, music, smart home
- youtube: YouTube link, summarise a video
- social_media: LinkedIn post, Twitter thread, newsletter
- decision: "should I", "which is better", "help me decide", pros and cons
- finance: spending, budget, "I spent X euros"
- research: research, papers, arXiv, Hacker News, studies
The denkschritte should be 2-4 short bullet points on how you will proceed.
Request: """

# Prompt for extracting key facts worth remembering
MEMORY_EXTRACT = """Extract the single most important fact worth remembering from this user message, as a short Key=Value pair.
Examples:
"Remember that my name is Sebastian" -> Name=Sebastian
"Keep in mind my dog is called Rex" -> Dog=Rex
"I live in Berlin" -> Location=Berlin
"Remember my favourite food is pizza" -> FavouriteFood=Pizza
Answer with the Key=Value pair ONLY, nothing else. Message: """


def _json_from_text(raw):
    """Extracts the first JSON object from a model reply — tolerant of markdown
    fences and prose around it. Returns a dict or None.

    v3.0: needed so classification also works with providers that (unlike
    Anthropic Haiku) like to write prose around the JSON.
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
    # fallback: cut out the first balanced {...}
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
# Keywords that hint at a specialist agent. If NONE of them appear, the request
# is almost certainly ordinary conversation and we skip the whole classification
# round trip (noticeably faster answers — especially on NVIDIA/OpenAI, where the
# classifier would otherwise have to bother the large main model). Conservative:
# when in doubt, classify.
_AGENT_KEYWORDS = (
    # calendar
    "appointment", "calendar", "meeting", "schedule", "event",
    # tasks
    "task", "to-do", "todo", "to do", "todoist", "notion",
    # email
    "e-mail", "email", "mail", "inbox", "mailbox",
    # briefing
    "briefing", "daily overview", "overview", "rundown",
    # document
    "pdf", "document", "file", ".docx", ".txt", "summari",
    # smarthome
    "light", "lamp", "temperature", "heating", "thermostat", "smart home",
    "smarthome", "socket", "blinds", "shutter",
    # media / youtube
    "youtube", "spotify", "music", "video", "playlist", "play ",
    # social media
    "linkedin", "twitter", "tweet", "thread", "newsletter", "instagram",
    # decision
    "should i", "which is better", "pros and cons", "pro/con",
    "help me decide", "decision", "decide",
    # finance
    "spent", "spending", "budget", "finances", "paid", "cost",
    "costs", "euro", "€", "invoice", "stock", "share price",
    # research
    "research", "paper", "arxiv", "hacker news", "study", "studies",
    # web search / current info
    "google", "search", "look up", "on the internet", "wikipedia",
    "weather", "news",
    # screen / computer control
    "screen", "screenshot", "click", "open ", "launch", "start ",
    "computer", "control",
)


def _is_simple_conversation(query: str) -> bool:
    """True when the request contains NO specialist-agent keyword and is
    therefore almost certainly ordinary conversation (skip the classifier)."""
    t = (query or "").lower()
    if not t.strip():
        return True
    return not any(kw in t for kw in _AGENT_KEYWORDS)


def default_classification(query: str) -> dict:
    """The default 'conversation' classification, with no API call (fast path)."""
    return {"agent": "conversation", "komplex": False, "sicherheitsrisiko": "none",
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
                logger.warning("[Brain] emit error (callback issue): %s", e)
            except RuntimeError as e:
                logger.warning("[Brain] emit error (runtime): %s", e)
            except Exception as e:  # noqa: BLE001 – last-resort guard for GUI stability
                logger.error("[Brain] emit error (unexpected): %s", e)

    def _step(self, label, text):
        """Sends one thinking step to the GUI."""
        self._emit("thinking_step", {"label": label, "text": text})

    # ── Klassifizierung ───────────────────────────────────────────────────
    def classify(self, query):
        default = {"agent":"conversation","komplex":False,"sicherheitsrisiko":"none",
                   "braucht_erlaubnis":False,"erlaubnis_grund":"","denkschritte":[],"zusammenfassung":query}
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        # v3.0: classification works across providers. It used to run ONLY through
        # the Anthropic client (self.client), so on NVIDIA/OpenAI/Gemini/local the
        # whole agent routing collapsed (everything became "conversation"). Now
        # Anthropic still uses the cheap, fast Haiku classifier model and all
        # other providers use their main model through decide_text().
        try:
            if provider == "anthropic" and self.client:
                classifier_model = self.config.get("classifier_model", "claude-haiku-4-5-20251001")  # Bug 1.16
                r = self.client.messages.create(
                    model=classifier_model, max_tokens=CLASSIFIER_MAX_TOKENS,  # Bug 1.21
                    messages=[{"role":"user","content": CLASSIFIER + query}])
                raw = r.content[0].text
            elif api_key or provider == "local":
                raw = self.decide_text(
                    "You are a request classifier. Answer EXCLUSIVELY with "
                    "a single valid JSON object – no markdown, no text around it.",
                    CLASSIFIER + query, max_tokens=CLASSIFIER_MAX_TOKENS)
            else:
                return default
            # Bug 1.5 / v3.0: robust JSON parsing (tolerates prose around the JSON)
            parsed = _json_from_text(raw)
            if isinstance(parsed, dict) and parsed.get("agent"):
                merged = dict(default)   # fill missing fields from the defaults
                merged.update(parsed)
                return merged
            logger.warning("[Brain] Classify: no valid JSON | raw: %s", str(raw)[:200])
            return default
        except anthropic.APIError as e:
            logger.error("[Brain] Classify API error: %s", e)
            return default
        except (httpx.HTTPError, ConnectionError) as e:
            logger.error("[Brain] Classify network error: %s", e)
            return default
        except Exception as e:  # noqa: BLE001 - classification must never stop the flow
            logger.error("[Brain] Classify unexpected error: %s", e)
            return default

    def _build_system(self):
        system = SYSTEM_PROMPTS.get(self.config.get("tone","normal"), SYSTEM_PROMPTS["normal"])
        salutation = self.config.get("salutation","")
        if salutation: system += f" Address the user as '{salutation}'."

        # language
        lang = self.config.get("language","de-DE")
        if lang != "de-DE":
            system += f" From now on, answer in {LANG_NAMES.get(lang,'English')}."

        # Location - IMPORTANT: use it, never ask for it
        location = self.config.get("location","")
        if location:
            system += (f"\n\nIMPORTANT: the user's location is '{location}'. "
                       f"ALWAYS use this location directly for weather, local info and so on. "
                       f"NEVER ask for the location - you already know it: {location}.")

        ctx = self.memory.get_relevant_context("")
        if ctx: system += f"\n\nStored facts about the user:\n{ctx}"
        scr = self.screen.get_description()
        if scr: system += f"\n\nBildschirm: {scr}"
        system += f"\n\nWhen you write code, always use markdown code blocks with a language tag (```python etc.). Format code cleanly with correct indentation. Check the logic mentally before you answer."
        system += f"\n\nAktuelle Zeit: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        return system

    # ── Hauptverarbeitung ─────────────────────────────────────────────────
    def process(self, query, todo_mode=False):
        if self.kill_event.is_set(): return None

        if not self._rate_limiter.check():
            msg = "⚠️ Too many requests - please wait a moment."
            self._emit("message", {"role":"jarvis","text": msg})
            return None

        provider = self.config.get("api_provider","anthropic")
        api_key  = self.config.get("api_key","")
        if not api_key and provider != "local":
            msg = "Please enter an API key in the settings (⚙) first."
            self._emit("message", {"role":"jarvis","text": msg})
            self._emit("needs_setup", {"reason":"No API key"})
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

        # a fast screen-request check (before classification)
        screen_kw = ["look at my screen","screenshot","check my screen",
                      "what do you see","look at my screen","help me i am stuck here",
                      "what do i click here","take a look","what is on my screen",
                      "can you see my screen","show me what i see","screen"]
        if any(kw in query.lower() for kw in screen_kw):
            self._step("VISION", "Screen request detected – starting the countdown...")
            self._emit("screenshot_countdown", {"query": query})
            return None

        # ── v3.0 Speed: Konversations-Schnellpfad ────────────────────────────
        # If the request holds NO specialist-agent keyword it is almost certainly
        # ordinary conversation, so skip the whole classifier round trip and
        # answer directly. Saves a whole LLM call on every chat turn.
        if _is_simple_conversation(query):
            cl = dict(default_classification(query))
            agent = "conversation"
            self._emit("agent_selected", {"agent": agent})
        else:
            # Klassifizierung
            self._emit("status", {"text":"🧠 Analysiere..."})
            self._step("ANALYSIS", "Understanding the request and picking the right agent...")
            cl = self.classify(query)
            agent = cl.get("agent","conversation")
            self._emit("agent_selected", {"agent": agent})

        # Thinking steps to the GUI
        for s in cl.get("denkschritte", []):
            self._step("PLANUNG", s)

        if cl.get("sicherheitsrisiko") == "hoch":
            self._step("SAFETY", "The risk is too high – the action was blocked.")
            self._emit("safety_warning", {"level":"high","message":"Action blocked – the risk is too high."})
            if todo_mode: return {"status":"error","message":"The safety risk is too high."}
            return None

        if cl.get("braucht_erlaubnis") and not todo_mode:
            self._emit("permission_request", {"grund": cl.get("erlaubnis_grund","")})
            return None

        # Screen Vision: Countdown → Screenshot → Analyse
        if agent == "screen_vision":
            self._step("VISION", "Starting screen capture with a countdown...")
            self._emit("screenshot_countdown", {"query": query})
            return None

        # ── Specialist agents with their own logic ───────────────────────
        special_result = self._handle_special_agent(agent, query)
        if special_result is not None:
            reply = special_result
            reply = self._process_code_in_reply(reply, self.config.get("api_provider","anthropic"), self.config.get("api_key",""), "", [])
            self._emit("message", {"role":"jarvis","text": reply})
            self.memory.add_to_history("user", query)
            self.memory.add_to_history("assistant", reply)
            self._step("FERTIG", "Antwort gesendet.")
            if any(kw in query.lower() for kw in ["remember that","remember","keep in mind","do not forget"]):
                self._save_key_memory(query)
            self._emit("status", {"text":"✅ BEREIT"})
            if todo_mode:
                return {"status":"ok","reply":reply}
            return reply

        self._step("EXECUTION", f"Agent [{agent}] is generating the answer...")

        system = self._build_system()
        hist = self.memory.get_conversation_history(8)
        messages = [{"role":m["role"],"content":m["content"]} for m in hist]
        messages.append({"role":"user","content": query})

        self._emit("status", {"text": f"⚡ [{agent.upper()}] antwortet..."})

        reply = ""
        try:
            reply = self._call(provider, api_key, system, messages)
        except anthropic.AuthenticationError:
            reply = "⚠️ API key invalid (401). Enter a new key in the settings (⚙)."
            self._emit("needs_setup", {"reason":"401"})
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response else "?"
            if code == 401: reply = "⚠️ API key invalid. Open the settings (⚙)."; self._emit("needs_setup",{"reason":"401"})
            elif code == 429: reply = "⚠️ Rate-Limit – kurz warten."
            else: reply = f"⚠️ HTTP error {code}."
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            reply = f"⚠️ Verbindungsfehler: {str(e)[:120]}"
            logger.error("[Brain] Verbindungsfehler: %s", e)
        except Exception as e:
            reply = f"⚠️ Error: {str(e)[:150]}"
            logger.error("[Brain] Error: %s", e)

        if reply:
            reply = self._process_code_in_reply(reply, provider, api_key, system, messages)
            self._emit("message", {"role":"jarvis","text": reply})
            self.memory.add_to_history("user", query)
            self.memory.add_to_history("assistant", reply)
            self._step("FERTIG", "Antwort gesendet.")

        # memory: extract key=value only
        if any(kw in query.lower() for kw in ["remember that","remember","keep in mind","do not forget"]):
            self._save_key_memory(query)

        self._emit("status", {"text":"✅ BEREIT"})
        if todo_mode:
            return {"status":"ok","reply":reply}
        return reply

    # ── Spezial-Agenten Handler ─────────────────────────────────────────
    def _handle_special_agent(self, agent, query):
        """Handles agents with their own logic. Returns text, or None for default handling."""

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

        # social_media, decision -> default handling with an adjusted system prompt
        if agent == "social_media":
            return self._agent_social_media(query)
        if agent == "decision":
            return self._agent_decision(query)

        return None

    def _agent_briefing(self, query):
        self._step("BRIEFING", "Collecting data for the briefing...")
        if not self.briefing:
            return "Briefing module unavailable."
        sections = self.briefing.generate_briefing()
        text = self.briefing.format_briefing(sections)
        # send it to the model to be phrased
        return self._enrich_with_model(text, "Phrase this briefing naturally and engagingly in English. Keep every piece of information:")

    def _agent_youtube(self, query):
        self._step("YOUTUBE", "Suche YouTube-Transkript...")
        if not self.youtube or not self.youtube.is_available:
            return "YouTube module unavailable. Install it with `pip install youtube-transcript-api`."
        video_id = self.youtube.extract_video_id(query)
        if not video_id:
            return "No YouTube link found in the message."
        prompt = self.youtube.get_video_summary_prompt(video_id)
        if not prompt:
            return "No transcript is available for that video."
        self._step("YOUTUBE", "Writing the summary...")
        return self._enrich_with_model(prompt, None)

    def _agent_finance(self, query):
        self._step("FINANZEN", "Verarbeite Finanz-Anfrage...")
        if not self.finance:
            return "Finance module unavailable."
        q = query.lower()
        if any(kw in q for kw in ["ausgegeben", "bezahlt", "gekauft", "gekostet"]):
            import re
            amount_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:€|euro|eur)', q)
            if amount_match:
                amount = float(amount_match.group(1).replace(",", "."))
                self.finance.add_expense(amount, "", query)
                return f"Recorded a {amount:.2f}€ expense.\n\n{self.finance.format_summary_text(30)}"
        if any(kw in q for kw in ["budget", "setze", "limit"]):
            return "Name a category and an amount, e.g. 'Set the groceries budget to 300 euros'."
        if any(kw in q for kw in ["overview", "summary", "spending", "how much"]):
            days = 7 if "woche" in q else 30
            return self.finance.format_summary_text(days)
        return self.finance.format_summary_text(30)

    def _agent_research(self, query):
        self._step("RECHERCHE", "Sammle aktuelle Artikel...")
        if not self.research:
            return "Research module unavailable."
        q = query.lower()
        import re
        detail_match = re.search(r'(?:more on|details on|article)\s*(\d+)', q)
        if detail_match:
            idx = int(detail_match.group(1))
            article = self.research.get_article_detail(idx)
            if article:
                return f"**{article['title']}**\n\n{article.get('summary', '')}\n\nLink: {article.get('link', '')}"
            return f"Article {idx} not found."
        articles = self.research.fetch_articles()
        text = self.research.format_research_text(articles)
        return f"**Research-Briefing:**\n\n{text}"

    def _agent_calendar(self, query):
        self._step("CALENDAR", "Checking the calendar...")
        if not self.calendar or not self.calendar.is_configured:
            return ("Google Calendar is not configured. Put `google_credentials.json` "
                    "into `~/.jarvis/` and restart My Jarvis.")
        q = query.lower()
        if any(kw in q for kw in ["create", "add", "new appointment", "appointment for"]):
            return self._calendar_create(query)
        days = 1
        if "woche" in q:
            days = 7
        elif "morgen" in q:
            days = 2
        events = self.calendar.get_events(days)
        text = self.calendar.format_events_text(events)
        period = "tomorrow" if "tomorrow" in q else ("this week" if days == 7 else "today")
        return f"**Termine {period}:**\n\n{text}"

    def _calendar_create(self, query):
        from core.calendar_integration import parse_datetime_natural
        # Send it to the model to extract the title and time
        extract_prompt = (
            f"Extract the appointment title and the time/date from this request. "
            f"Answer with JSON ONLY: {{\"title\": \"...\", \"datetime\": \"YYYY-MM-DDTHH:MM\", \"duration\": 60}}\n"
            f"Anfrage: {query}"
        )
        try:
            result = self._call_sync(
                self.config.get("api_provider", "anthropic"),
                self.config.get("api_key", ""),
                "You are a JSON extractor. Answer with valid JSON ONLY.",
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
                    return f"Appointment created: **{event['title']}** am {event['start']}"
        except Exception as e:
            logger.debug("[Brain] Calendar create parse error: %s", e)

        dt = parse_datetime_natural(query)
        if dt:
            event = self.calendar.create_event(query[:50], dt)
            if event:
                return f"Appointment created: **{event['title']}** am {event['start']}"
        return "Could not create the appointment. Give me a title and a date/time."

    def _agent_tasks(self, query):
        self._step("TASKS", "Checking tasks...")
        if not self.tasks or not self.tasks.is_configured:
            return "Task management is not configured. Add a Todoist or Notion API key in the settings."
        q = query.lower()
        if any(kw in q for kw in ["add", "new task", "create task"]):
            content = query
            for prefix in ["add to todoist:", "add:", "new task:", "create task:"]:
                if prefix in q:
                    content = query[q.index(prefix) + len(prefix):].strip()
                    break
            result = self.tasks.add_task(content)
            if result:
                return f"Task created: **{result['content']}** [{result['source']}]"
            return "Could not create the task."
        task_list = self.tasks.get_all_tasks()
        return f"**Offene Aufgaben ({self.tasks.get_provider_name()}):**\n\n{self.tasks.format_tasks_text(task_list)}"

    def _agent_email(self, query):
        self._step("EMAIL", "Checking email...")
        if not self.email or not self.email.is_configured:
            return "Email is not configured. Add the IMAP server, address and app password in the settings."
        q = query.lower()
        if any(kw in q for kw in ["send", "send it", "confirm"]):
            if self.email.get_pending_draft():
                if self.email.send_pending_draft():
                    return "The email was sent."
                return "Failed to send the email."
        if any(kw in q for kw in ["zusammenfas", "ungelesen", "posteingang", "mails"]):
            emails = self.email.get_unread(5)
            text = self.email.format_emails_text(emails)
            if not emails:
                return text
            return self._enrich_with_model(
                f"Summarise these unread emails:\n\n{text}",
                "Write a compact summary of these emails in English:"
            )
        return "What would you like to do with your email? (summarise, write a reply...)"

    def _agent_document(self, query):
        self._step("DOKUMENT", "Analysiere Dokument...")
        if not self.doc_reader:
            return "Document module unavailable."
        import re
        path_match = re.search(r'[A-Za-z]:[/\\][^\s]+|/[^\s]+|~[/\\][^\s]+', query)
        if not path_match:
            return "Give me the file path, e.g. 'Summarise C:\\Documents\\report.pdf'"
        filepath = path_match.group(0)
        text = self.doc_reader.read(filepath)
        if not text:
            return f"Could not read '{filepath}'. Supported formats: {', '.join(self.doc_reader.get_supported_extensions())}"
        from core.document_reader import DocumentReader
        chunks = DocumentReader.chunk_text(text)
        if len(chunks) == 1:
            return self._enrich_with_model(
                f"Summarise this document:\n\n{chunks[0][:6000]}",
                "Write a structured summary in English:"
            )
        chunk_text = "\n\n---\n\n".join(f"Section {i+1}:\n{c[:2000]}" for i, c in enumerate(chunks[:5]))
        return self._enrich_with_model(
            f"Summarise this document ({len(chunks)} sections):\n\n{chunk_text}",
            "Write an overall summary in English:"
        )

    def _agent_smarthome(self, query):
        self._step("SMART HOME", "Controlling devices...")
        if not self.smarthome or not self.smarthome.is_configured:
            return self.smarthome.get_status_text() if self.smarthome else "Smart home module unavailable."
        # Einfache Keyword-Erkennung, komplexere via Modell
        q = query.lower()
        if any(kw in q for kw in ["lights on", "light on"]):
            lights = self.smarthome.get_entities("light")
            if lights:
                self.smarthome.light_on(lights[0]["entity_id"])
                return f"Licht eingeschaltet: {lights[0]['name']}"
        elif any(kw in q for kw in ["lights off"]):
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
                    return f"Temperature set to {temp}°C: {climate[0]['name']}"
        elif any(kw in q for kw in ["musik", "spotify", "spiele"]):
            media = self.smarthome.get_entities("media_player")
            if media:
                self.smarthome.play_media(media[0]["entity_id"])
                return f"Playback started: {media[0]['name']}"
        return self.smarthome.get_status_text()

    def _agent_social_media(self, query):
        q = query.lower()
        if "linkedin" in q:
            platform_prompt = ("Write a professional LinkedIn post on the given topic. "
                              "150-300 words, 3-5 relevant hashtags, emojis sparingly. "
                              "Professioneller, inspirierender Ton.")
        elif "twitter" in q or "thread" in q:
            platform_prompt = ("Write a Twitter/X thread on the given topic. "
                              "Max 280 characters per tweet. 5-8 tweets, numbered (1/N). "
                              "Punchy and engaging.")
        elif "newsletter" in q:
            platform_prompt = ("Draft a newsletter on the given topic. "
                              "Structured with an intro, a main part and a call to action. Max 500 words.")
        else:
            platform_prompt = ("Write a social media post on the given topic. "
                              "Suited to the platform, professional tone.")
        return self._enrich_with_model(query, platform_prompt)

    def _agent_decision(self, query):
        ctx = self.memory.get_relevant_context("")
        decision_prompt = (
            "Help with this decision through a structured pros-and-cons analysis. "
            "Format:\n"
            "1. Optionen identifizieren\n"
            "2. Pros and cons for each option (table or list)\n"
            "3. A clear recommendation with reasoning\n"
        )
        if ctx:
            decision_prompt += f"\nContext about the user:\n{ctx}"
        return self._enrich_with_model(query, decision_prompt)

    def _enrich_with_model(self, content, instruction):
        """Sends content to the model to be phrased naturally."""
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        if not api_key and provider != "local":
            return content

        system = instruction or self._build_system()
        messages = [{"role": "user", "content": content}]

        try:
            return self._call(provider, api_key, system, messages)
        except Exception as e:
            logger.warning("[Brain] enrichment failed: %s", e)
            return content

    # ── Memory: Key=Value Extraktion ──────────────────────────────────────
    def _save_key_memory(self, query):
        # v3.0: works across providers. It used to be Anthropic only (self.client),
        # so on NVIDIA/OpenAI/Gemini/local "remember that ..." was ignored entirely.
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
                    "You extract the single most important fact worth remembering as a "
                    "short Key=Value pair. Answer with the Key=Value pair ONLY.",
                    MEMORY_EXTRACT + query, max_tokens=MEMORY_EXTRACT_MAX_TOKENS)
                kv = (raw or "").strip().split("\n")[0] if raw else None
        except anthropic.APIError as e:
            logger.error("[Brain] Memory extract API error: %s", e)
        except (httpx.HTTPError, ConnectionError) as e:
            logger.error("[Brain] Memory extract network error: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.error("[Brain] Memory extract error: %s", e)
        if kv and "=" in kv:
            self.memory.save_memory_kv(kv)
            self._emit("memory_saved", {"query": kv})
            logger.info("[Memory] key info stored: %s", kv)
        else:
            logger.debug("[Memory] Could not extract a key=value pair: %s", query[:60])

    # ── Screenshot-Analyse ─────────────────────────────────────────────
    def analyze_screenshot(self, screenshot_b64: str, query: str = "") -> str:
        provider = self.config.get("api_provider", "anthropic")
        api_key = self.config.get("api_key", "")
        if not api_key and provider != "local":
            return "No API key available. Enter one in the settings."

        self._step("VISION", "Analysing the screenshot...")

        salutation = self.config.get("salutation", "")
        salutation_str = f" Address the user as '{salutation}'." if salutation else ""
        system = (
            f"You are My Jarvis, an AI assistant that can see the user's screen.{salutation_str} "
            "Describe EXACTLY what you see and give specific instructions with positions "
            "(e.g. 'top left', 'in the middle', 'bottom right'). "
            "Be precise: name button labels, menu items, window titles and so on."
        )
        user_text = query if query else "What do you see on my screen? Describe what you see."

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
                    return "Error: the API response contains no 'choices'."
                choice = data["choices"][0]
                if not isinstance(choice, dict) or "message" not in choice or "content" not in choice.get("message", {}):
                    return "Error: unexpected format in the API response."
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
                    return "Error: the Gemini response contains no 'candidates'."
                try:
                    reply = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, TypeError) as e:
                    logger.error("[Brain] Gemini Vision response parse error: %s", e)
                    return "Error: unexpected Gemini response format."

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
                    return "Error: the Ollama response contains no 'message.content'."
                reply = data["message"]["content"]
            else:
                reply = "Unknown provider for screen analysis."

            self._step("FERTIG", "Screenshot analysiert.")
            self.memory.add_to_history("user", f"[Screenshot] {user_text}")
            self.memory.add_to_history("assistant", reply)
            return reply
        except anthropic.APIError as e:
            logger.error("[Brain] Vision API error: %s", e)
            return f"API error during screen analysis: {str(e)[:150]}"
        except requests.exceptions.Timeout:
            logger.error("[Brain] Vision Timeout")
            return "Error: screen analysis timed out."
        except requests.exceptions.ConnectionError as e:
            logger.error("[Brain] Vision Verbindungsfehler: %s", e)
            return f"Connection error during screen analysis: {str(e)[:150]}"
        except Exception as e:
            logger.error("[Brain] Vision error: %s", e)
            return f"Error during screen analysis: {str(e)[:150]}"

    # ── Vision-Entscheidung (Copilot) ──────────────────────────────────────
    def vision_decide(self, screenshot_b64: str, system: str, user_text: str,
                      max_tokens: int = 700) -> str:
        """A lean multi-provider vision call for the copilot.

        Returns the RAW TEXT of the model reply (typically JSON).
        No memory or step side effects — the copilot parses it itself.
        Raises NO exceptions for network or API errors; returns an empty string
        instead, so the copilot loop can react cleanly.
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
                    # v2.9: the old llama-3.2-90b-vision refuses PC control and returns
                    # no JSON. llama-4-maverick is multimodal, robust and follows the
                    # JSON instruction. Overridable through nvidia_vision_model.
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
            logger.error("[Brain] vision_decide API error: %s", e)
            return ""
        except (requests.exceptions.RequestException, httpx.HTTPError) as e:
            logger.error("[Brain] vision_decide network error: %s", e)
            return ""
        except (KeyError, IndexError, TypeError) as e:
            logger.error("[Brain] vision_decide parse error: %s", e)
            return ""

    def decide_text(self, system: str, user_text: str,
                    max_tokens: int = 300) -> str:
        """v2.9: a lean multi-provider TEXT call (no image) for the copilot.

        Used for example to pick the right entry from a process or app list
        (spec 1a), or for self-reflection without a screenshot.
        Returns the RAW TEXT; on errors an empty string, never an exception.
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
            logger.error("[Brain] decide_text API error: %s", e)
            return ""
        except (requests.exceptions.RequestException, httpx.HTTPError) as e:
            logger.error("[Brain] decide_text network error: %s", e)
            return ""
        except (KeyError, IndexError, TypeError) as e:
            logger.error("[Brain] decide_text parse error: %s", e)
            return ""

    # ── To-do execution ───────────────────────────────────────────────────
    def run_todo_item(self, item_text):
        """Runs a single to-do task. Returns a dict."""
        if self.kill_event.is_set():
            return {"status":"error","message":"The kill switch is active."}
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
        if not self.client: return "Anthropic client not initialised."
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
            return "Error: the API response contains no valid 'choices'."
        choice = data["choices"][0]
        if not isinstance(choice, dict) or "message" not in choice:
            return "Error: unexpected response format (no 'message' in choice)."
        msg = choice["message"]
        if not isinstance(msg, dict) or "content" not in msg:
            return "Error: unexpected response format (no 'content' in message)."
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
            return "Error: the Gemini response contains no 'candidates'."
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            logger.error("[Brain] Gemini response parse error: %s", e)
            return "Error: unexpected Gemini response format."

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
                f"This {lang} code has a syntax error:\n"
                f"```{lang}\n{code}```\n"
                f"Error: {error_msg}\n"
                f"Return ONLY the corrected code, in a ```{lang} code block."
            )

            try:
                fix_reply = self._call_sync(
                    provider, api_key,
                    "You are a code-fixing assistant. Return ONLY corrected code.",
                    [{"role": "user", "content": fix_prompt}]
                )
            except Exception as e:
                logger.warning("[Brain] Code fix attempt %d failed: %s", attempt + 1, e)
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
            return "Error: the Ollama response has no 'message' field."
        msg = data["message"]
        if not isinstance(msg, dict) or "content" not in msg:
            return "Error: the Ollama response has no 'content' in 'message'."
        return msg["content"]
