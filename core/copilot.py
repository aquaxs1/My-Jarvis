"""
My Jarvis Copilot — AI-driven PC control
========================================
See -> Think -> Act -> Repeat.

The copilot takes a task in natural language, captures a screenshot, sends it to
the vision model, gets back ONE structured action as JSON, runs it through the
executor and carries on — until the model reports "done" or the user stops it.

Safety:
- Destructive actions (deleting, sending, buying, shell commands) need
  confirmation, unless the user chose "always allow".
- Password fields are NEVER filled automatically (a hard rule, not
  overridable). The copilot pauses and asks the user.
- After MAX_STEPS steps the copilot pauses and asks ("always allow" lifts
  that). HARD_MAX is the absolute ceiling.
- The stop button and the kill switch interrupt at any time.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import platform
from urllib.parse import quote_plus

from core import app_index   # v2.9: universal program/browser detection

logger = logging.getLogger("jarvis.copilot")

# ── Constants ────────────────────────────────────────────────────────────────
VISION_WIDTH   = 1280      # screenshots are scaled to this width (model input)
MAX_STEPS      = 15        # checkpoint interval: ask again after this many steps
HARD_MAX       = 60        # absolute ceiling (even with "always allow")
ACTION_SETTLE  = 0.6       # seconds of pause after each action (let the UI react)
DECISION_TOKENS = 700      # max_tokens for the model decision
HISTORY_KEEP   = 8         # how many past steps go into the prompt
PLAN_TOKENS    = 700       # max_tokens for the planning call (v2.7 #2)
VERIFY_SETTLE  = 0.8       # seconds to wait before checking the effect (v2.7 #3)
CLICK_RETRY_OFFSETS = ((5, 5), (-5, -5))  # pixel offsets for click retries (v2.7 #3)

# actions that use a target coordinate pair
_COORD_ACTIONS = {"click", "double_click", "right_click", "drag"}

# terms that hint at destructive/sensitive actions (a backstop on top of the
# model's own "destructive" flag).
_DESTRUCTIVE_KW = (
    "delete", "erase", "remove", "wipe", "format", "formatting",
    "buy", "order", "pay", "purchase", "checkout", "subscribe",
    "transfer", "send", "submit", "post ", "publish",
    "uninstall", "shutdown", "shut down", "restart", "reboot",
    "rm -rf", "drop table", "factory reset", "reset",
)

# terminal actions that end the loop
_TERMINAL = {"done", "fail"}

# ── v2.7 #4: Web-Intent ───────────────────────────────────────────────────────
# trigger words (case-insensitive) that hint at a web task.
_WEB_TRIGGERS = (
    "google", "look up", "open the website", "go to", "browser",
    "show me", "what is", "how does", "where can i", "search",
    "on the internet", "online", "website", "open the page",
    "search for", "look for", "find", "youtube", "wikipedia",
)
# domain detection (URL vs. search term)
_DOMAIN_RE = re.compile(
    r"((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*\."
    r"(?:com|de|org|net|io|gov|edu|co|info|tv|me|at|ch|eu|news|app|dev))(?:/\S*)?",
    re.IGNORECASE,
)
# browser preference order (spec): Chrome → Brave → Opera → Firefox → Edge → DuckDuckGo
_BROWSER_ORDER = ["Chrome", "Brave", "Opera", "Firefox", "Edge", "DuckDuckGo"]

# ── v2.7 #3: Media / Verifikation ─────────────────────────────────────────────
_MEDIA_KW = (
    "spotify", "youtube", "music", "song", "track", "listen",
    "play", "video", "movie", "netflix", "twitch", "podcast", "playlist",
)
# actions verified after execution (click accuracy is critical)
_VERIFY_CLICK = {"click", "double_click", "right_click"}
# actions where a change on screen alone counts as success
# (including the v2.8 aliases press_enter/space/scroll_*).
_VERIFY_CHANGE = {
    "type", "key", "open_program", "launch_app", "drag",
    "press_enter", "enter", "press_space", "space",
    "scroll_down", "scroll_up", "key_combo",
}
# hint for the model on media players (play-button handling) — v2.8 bug 4C:
# keep opening and playing strictly separate.
_MEDIA_HINT = (
    "MEDIA TASK (e.g. Spotify/YouTube):\n"
    "- Opening and PLAYING always in SEPARATE steps — never both in one click:\n"
    "  1) Click the playlist/track (open it) -> action \"wait\" -> check the screenshot: is it open?\n"
    "  2) ONLY THEN click the play button.\n"
    "- Hit the play button with \"action\":\"double_click\". If nothing happens, the "
    "space bar (key [\"space\"]) is the reliable play/pause toggle.\n"
    "- Success = the play button has turned into a pause button (music/video is running)."
)

# ── v2.8 bug 1: detect running programs (step 0) ──────────────────────────────
# maps common app names -> process name for the psutil check.
PROCESS_NAMES = {
    "spotify":  "Spotify.exe",
    "chrome":   "chrome.exe",
    "firefox":  "firefox.exe",
    "brave":    "brave.exe",
    "opera":    "opera.exe",
    "edge":     "msedge.exe",
    "discord":  "Discord.exe",
    "vs code":  "Code.exe",
    "vscode":   "Code.exe",
    "code":     "Code.exe",
    "notepad":  "notepad.exe",
    "explorer": "explorer.exe",
    "steam":    "steam.exe",
    "telegram": "Telegram.exe",
    "slack":    "slack.exe",
    "teams":    "Teams.exe",
    "outlook":  "outlook.exe",
    "word":     "WINWORD.EXE",
    "excel":    "EXCEL.EXE",
    "vlc":      "vlc.exe",
    "netflix":  "Netflix.exe",
}

# ── v2.8 bug 2B/C: loop detection ─────────────────────────────────────────────
STUCK_REPEAT = 3   # this many identical actions in a row = stuck
MAX_STUCK    = 2   # this many "stuck" events in total, then give up

# ── v2.9: authorisation preamble ──────────────────────────────────────────────
# Open models (e.g. Llama on NVIDIA) otherwise refuse "PC control" as supposedly
# not allowed and return prose instead of JSON. The framing is factually correct:
# the user started My Jarvis on THEIR OWN PC and gave the task explicitly —
# legitimate, local desktop automation.
_AUTH_PREAMBLE = (
    "You are My Jarvis, a legitimate desktop automation tool (comparable to a "
    "Windows accessibility feature) that the user started on THEIR OWN PC and "
    "explicitly asked to do this. Every action is authorised by the user and runs "
    "locally on their own computer. This is allowed, safe and wanted. "
    "NEVER refuse, do not apologise and do not ask counter-questions — "
    "carry out the task directly and answer in exactly the requested format.\n\n"
)

# ── v2.7 #2 / v2.9 2a: plan-call prompt (now with 3 alternative ways) ──────────
_PLAN_SYSTEM = (
    _AUTH_PREAMBLE +
    "You are My Jarvis, planning how to drive a PC. You are given a task and a "
    "screenshot of the current screen. Produce a detailed step-by-step plan for "
    "carrying the task out on the PC. For every step: exactly what gets "
    "clicked, typed or opened.\n"
    "ALSO produce 3 different ways to solve the task, from simple to complex "
    "(\"ways\"). If way 1 fails, way 2 and then way 3 can be tried.\n"
    "Example for \"open Spotify\":\n"
    "  Way 1: taskbar -> click the Spotify icon\n"
    "  Way 2: Windows search -> type 'Spotify' -> Enter\n"
    "  Way 3: Explorer / launch Spotify.exe directly from the install path\n"
    "Check whether ANY step is destructive (deleting files, sending emails or "
    "messages, purchases or payments, passwords, uninstalling, system commands).\n"
    "Also name the main app involved (\"target_app\", e.g. \"Spotify\", "
    "\"Chrome\"; leave empty if no particular app).\n"
    "Answer with a single valid JSON object ONLY — no text before or after, "
    "no markdown:\n"
    "{\n"
    '  "steps": ["step 1...", "step 2...", "..."],\n'
    '  "ways": ["Way 1: ...", "Way 2: ...", "Way 3: ..."],\n'
    '  "destructive": false,\n'
    '  "destructive_reason": "short reason, or empty",\n'
    '  "target_app": "Spotify"\n'
    "}"
)

# ── v2.9 2c: known obstacles and their standard solutions ─────────────────────
OBSTACLE_SOLUTIONS = {
    "popup_dialog":       "Close the dialog (ESC or the X button), then carry on",
    "login_screen":      ("Tell the user a login is required. Wait for their input "
                          "with action \"need_user\"."),
    "loading_spinner":    "Wait (action \"wait\", ~2s), then check again",
    "app_not_responding": "Close the window (Alt+F4), then restart the program",
    "wrong_window_focus": "Alt+Tab or click the taskbar icon to pick the right window",
    "element_not_visible":"Scroll, or maximise the window, then look for the element again",
    "search_no_results":  "Simplify the search term, or try an alternative one",
    "permission_dialog":  "Ask the user BEFORE clicking 'Yes/Allow'",
    "app_not_found":      "Try another way to open it (launch_app with a path / desktop icon / suggest an alternative)",
}

# ── v2.9 2e: creative fallbacks per scenario ──────────────────────────────────
CREATIVE_FALLBACKS = {
    "cant_open_app": [
        "Scan the desktop for the icon (analyse the screenshot) and double-click it",
        "Search the taskbar",
        "Use launch_app with the exact .exe/.lnk path (the hint may contain it)",
        "Open File Explorer and start the .exe directly",
    ],
    "cant_click_button": [
        "Maximise the window, then try again",
        "Use Tab to navigate to the button, then Enter",
        "Right-click the element -> the matching context menu option",
        "A keyboard shortcut instead of a click (e.g. Ctrl+P instead of the Print button)",
    ],
    "cant_type_text": [
        "Click into the field first, then type",
        "Double-click the text field",
        "Focus the field with the Tab key",
        "Ctrl+A to select the old content, then retype",
    ],
    "website_not_loading": [
        "Press F5 (reload)",
        "Focus the address bar (Ctrl+L) and re-enter the URL",
        "Try a different browser",
        "Ctrl+Shift+Del -> clear the cache -> try again",
    ],
}

# ── v2.9 2d: self-reflection after several failed attempts ────────────────────
REFLECT_AFTER   = 3   # reflect after this many ineffective steps in a row
MAX_REFLECTIONS = 2   # reflect this often, then stop honestly
_REFLECT_SYSTEM = (
    "You are My Jarvis and you are stuck on a PC task. Several attempts have "
    "achieved nothing. Think FUNDAMENTALLY DIFFERENTLY and find a completely new "
    "approach.\n"
    "Analyse honestly:\n"
    "1. What could be the reason it has not worked so far?\n"
    "2. What COMPLETELY different approach exists (another tool/menu/shortcut)?\n"
    "3. What would an experienced person do in this situation?\n"
    "Answer as JSON: {\"reason\": \"...\", \"new_approach\": \"concrete, and "
    "clearly different from the previous attempts\"}"
)


def _render_playbook() -> str:
    """v2.9: OBSTACLE_SOLUTIONS + CREATIVE_FALLBACKS, compact, for the system prompt."""
    obs = "\n".join(f"  • {k}: {v}" for k, v in OBSTACLE_SOLUTIONS.items())
    fb = "\n".join(
        f"  • {scen}: " + " | ".join(opts)
        for scen, opts in CREATIVE_FALLBACKS.items()
    )
    return (
        "WORK AROUND OBSTACLES AUTOMATICALLY — when you spot one, set the field "
        "\"obstacle\" to the matching type AND apply the standard solution right away "
        "(do not ask the user, except for permission_dialog):\n"
        f"{obs}\n"
        "CREATIVE FALLBACKS — when something does not work, try these in order:\n"
        f"{fb}"
    )


# rendered once for the system prompt (static).
_PLAYBOOK = _render_playbook()


class Copilot:
    """See→Think→Act loop. One instance, only ever one run at a time."""

    def __init__(self, brain, executor, screen, kill_event, emit_cb=None):
        self.brain      = brain
        self.executor   = executor
        self.screen     = screen
        self.kill_event = kill_event
        self.emit       = emit_cb or (lambda *_a, **_k: None)
        self.os         = platform.system()

        self._running   = False
        self._stop      = threading.Event()

        # handshake with the GUI (confirmation / checkpoint / user input)
        self._resp_event = threading.Event()
        self._resp       = None

        # runtime flags
        self.allow_all    = False   # skips checkpoints
        self.auto_confirm = False   # confirms destructive actions automatically
        self.web_hint     = ""      # v2.7 #4: web-task hint for the prompt
        self.media_task   = False   # v2.7 #3: media task (play-button handling)
        self.target_app   = ""      # v2.8 #3: main app from the plan
        self.app_running_hint = ""  # v2.8 #1: hint "app already running → foreground"
        self._pending_hint = ""     # v2.8 #2: one-off hint for the next THINK

        # v2.9: extended reasoning + universal program detection
        self.plan_ways      = []    # 2a: 3 alternative ways from the plan
        self.current_way    = 0     # 2a: which way is being followed right now
        self.installed_hint = ""    # 1b/1d: launch path of the target app, for the model
        self.app_alternatives = []  # 1d: suggestions when the app was not found
        self.ineffective_streak = 0 # 2d: ineffective steps in a row
        self.reflections    = 0     # 2d: how many times it has reflected already
        self.last_effective = True  # 2b: did the last step achieve anything?
        self.launch_path    = ""    # v2.9 speed: resolved .exe/.lnk path of the target app
        self.launch_name    = ""    # v2.9 speed: display name of the target app

        # v2.9 part 1: scan installed programs in the background (fill the cache)
        try:
            app_index.prewarm()
        except Exception as e:  # noqa: BLE001 – must never break startup
            logger.info("[Copilot] App index prewarm skipped: %s", e)

        # v2.7 #1: Desktop-Overlay ("JARVIS is Controlling your PC").
        # Created here (not shown) – falls back to None quietly when headless.
        self.overlay = None
        try:
            from core.copilot_overlay import CopilotOverlay
            self.overlay = CopilotOverlay(on_stop=self.stop)
        except Exception as e:  # noqa: BLE001 – tkinter may not be available
            logger.info("[Copilot] Desktop overlay not available: %s", e)

    # ── Status ────────────────────────────────────────────────────────────
    @property
    def is_running(self) -> bool:
        return self._running

    # ── control from outside (GUIServer) ────────────────────────────────────
    def stop(self):
        """Aborts the running job and releases any waiting handshakes."""
        self._stop.set()
        self._resp = None
        self._resp_event.set()

    def resolve(self, decision: str):
        """The GUI's answer to a handshake.
        decision ∈ {allow, always, deny, continue, stop, user_done}.
        """
        self._resp = decision
        self._resp_event.set()

    # ── main loop (blocking – start it in a thread) ──────────────────────────
    def run(self, task: str, allow_all: bool = False):
        task = (task or "").strip()
        if not task:
            self._done("fail", "No task given.")
            return
        if self._running:
            self.emit("copilot_status", {"text": "The copilot is already running.", "phase": "busy"})
            return

        self._running   = True
        self._stop.clear()
        self.allow_all    = bool(allow_all)
        self.auto_confirm = bool(allow_all)
        # v2.8/v2.9: reset the run state
        self.target_app = ""
        self.app_running_hint = ""
        self._pending_hint = ""
        self.plan_ways = []
        self.current_way = 0
        self.installed_hint = ""
        self.app_alternatives = []
        self.ineffective_streak = 0
        self.reflections = 0
        self.last_effective = True
        self.launch_path = ""
        self.launch_name = ""

        # ── v2.7 #4 / v2.9 part 3: web intent + universal browser detection ──
        tl = task.lower()
        self.media_task = any(kw in tl for kw in _MEDIA_KW)
        web = detect_web_intent(task)
        self.web_hint = ""
        if web["is_web"]:
            browser = _resolve_browser()
            self.web_hint = self._build_web_hint(web, browser)
            print(f"[Copilot] Web task detected → target: {web['target']} | "
                  f"browser: {browser.get('name', '—')}"
                  f"{' (running)' if browser.get('running') else ''}")

        self.emit("copilot_started", {"task": task, "allow_all": self.allow_all})
        self.emit("copilot_step", {"step": 0, "action": "start",
                                   "status": f"Task received: {task}", "result": ""})
        print(f"[Copilot] Start. Task: {task!r} | allow_all={self.allow_all}")

        # ── v2.7 #1: show the desktop overlay ────────────────────────────────
        if self.overlay:
            try:
                self.overlay.show(task)
            except Exception:  # noqa: BLE001 – the overlay must never break the run
                pass

        # ── v2.9 speed: plan ONLY for complex/multi-step/sensitive tasks ─────
        # Simple single actions (e.g. "open Spotify") need no plan up front —
        # that saves a whole vision call and makes the copilot noticeably faster.
        if self._should_plan(task):
            if not self._plan_phase(task):
                return
        else:
            self.plan_ways = []
            self.emit("copilot_plan", {"steps": [], "ways": [], "destructive": False,
                                       "reason": "", "target_app": ""})
            self._status("Simple task – starting right away (no plan).", "plan", 0)
            print("[Copilot] Simple task → no plan up front (faster).")

        # ── v2.8 bug 1: step 0 – is the target app already running? ──────────
        self._detect_running_app(task)
        # ── v2.9 1b/1d: not running → install path / alternatives ───────────
        self._resolve_target_app(task)

        loop = asyncio.new_event_loop()
        history = []
        action_sigs = []   # v2.8 bug 2: signatures of the last actions
        stuck_events = 0   # v2.8 bug 2: how many "stuck" events so far
        step = 0
        since_check = 0

        try:
            # ── v2.9 speed: fast launch for plain "open program" tasks ───────
            # If the app is installed (path known) and not yet running, we start it
            # straight through launch_app – with NO vision call at all. If that has
            # no visible effect, the normal See→Think→Act loop takes over.
            if self._try_fast_launch(loop, task):
                return

            while self._running and not self._stop.is_set():
                if self.kill_event.is_set():
                    self._done("stopped", "Kill switch active – copilot stopped.")
                    return

                # ── checkpoint after MAX_STEPS steps ─────────────────────────
                if since_check >= MAX_STEPS and not self.allow_all:
                    r = self._wait("copilot_checkpoint", {"step": step})
                    if r in (None, "stop"):
                        self._done("stopped", f"Stopped on request after {step} steps.")
                        return
                    if r == "always":
                        self.allow_all = True
                        self.auto_confirm = True
                    since_check = 0

                if step >= HARD_MAX:
                    self._done("fail", f"Maximum number of steps ({HARD_MAX}) reached. Stopping.")
                    return

                step += 1
                since_check += 1

                # ── SEE ──────────────────────────────────────────────────────
                self._status("Looking at the screen…", "see", step)
                shot = self.screen.capture_for_vision(target_width=VISION_WIDTH)
                if not shot:
                    self._done("fail", "Screenshot failed (is PyAutoGUI/PIL missing?).")
                    return
                b64, vw, vh, rw, rh = shot

                if self._stopped():
                    return

                # ── THINK ────────────────────────────────────────────────────
                self._status("Working out the next step…", "think", step)
                decision = self._think(task, b64, vw, vh, history)
                self._pending_hint = ""   # v2.8: the one-off hint has been used up
                if decision is None:
                    self._done("fail", "Could not reach a valid decision.")
                    return

                action = str(decision.get("action", "")).strip().lower()
                status_text = (decision.get("status")
                               or decision.get("reasoning")
                               or action or "…")

                self.emit("copilot_step", {
                    "step": step, "action": action, "status": status_text,
                    "observation": decision.get("observation", ""), "result": "",
                })
                print(f"[Copilot] Step {step}: {action} — {status_text}")

                entry = {"step": step, "action": action, "status": status_text, "result": "—"}
                history.append(entry)

                if self._stopped():
                    return

                # ── terminal actions ─────────────────────────────────────────
                if action == "done" or decision.get("done") is True:
                    self._done("done", decision.get("summary") or "Task completed.")
                    return
                if action == "fail":
                    self._done("fail", decision.get("summary") or "I cannot get any further here.")
                    return

                # ── user input needed (e.g. a login) ────────────────────────
                if action == "need_user":
                    msg = decision.get("summary") or "Please enter the required input manually."
                    if not self._ask_user(msg):
                        self._done("stopped", "Stopped by the user.")
                        return
                    entry["result"] = "The user took over."
                    continue

                # ── password field: HARD rule – never fill it automatically ──
                if decision.get("password_field") and action in ("type", "key", "click"):
                    msg = ("Password field detected. For safety I will not fill it in "
                           "automatically. Please type your password yourself and then "
                           "click \"Continue\".")
                    if not self._ask_user(msg):
                        self._done("stopped", "Stopped by the user.")
                        return
                    entry["result"] = "Password entered manually."
                    continue

                # ── destructive action: ask for confirmation ─────────────────
                if self._is_destructive(action, decision):
                    if not self.auto_confirm:
                        detail = self._describe(action, decision)
                        r = self._wait("copilot_confirm",
                                       {"step": step, "action": action, "detail": detail})
                        if r in (None, "deny"):
                            entry["result"] = "Rejected by the user – skipped."
                            self.emit("copilot_step", {
                                "step": step, "action": action,
                                "status": "Action rejected – looking for another way.",
                                "result": "rejected"})
                            continue
                        if r == "always":
                            self.auto_confirm = True

                if self._stopped():
                    return

                # ── v2.9 2c: obstacle spotted? apply the standard solution ──
                obstacle = str(decision.get("obstacle", "")).strip().lower()
                if obstacle and obstacle in OBSTACLE_SOLUTIONS:
                    self.emit("copilot_step", {
                        "step": step, "action": action,
                        "status": f"Obstacle detected ({obstacle}) – "
                                  f"{OBSTACLE_SOLUTIONS[obstacle]}",
                        "result": "obstacle"})
                    print(f"[Copilot] Obstacle: {obstacle} -> "
                          f"{OBSTACLE_SOLUTIONS[obstacle]}")
                    # safety exception: never blindly confirm a permission dialog
                    if obstacle == "permission_dialog" and not self.auto_confirm:
                        r = self._wait("copilot_confirm", {
                            "step": step, "action": action,
                            "detail": "A permission/security dialog has appeared. "
                                      "Should I continue?"})
                        if r in (None, "deny"):
                            entry["result"] = "Permission denied – skipped."
                            self._pending_hint = (
                                "The user denied the permission dialog. "
                                "Close it (ESC) and look for another way.")
                            self.emit("copilot_step", {
                                "step": step, "action": action,
                                "status": "Permission denied – trying another way.",
                                "result": "rejected"})
                            continue
                        if r == "always":
                            self.auto_confirm = True

                # ── v2.8 bug 2B/C: stuck detection (same action 3 times) ─────
                action_sigs.append(self._action_sig(action, decision))
                if (len(action_sigs) >= STUCK_REPEAT
                        and len(set(action_sigs[-STUCK_REPEAT:])) == 1):
                    stuck_events += 1
                    action_sigs.clear()
                    if stuck_events >= MAX_STUCK:
                        self._done("fail", "I am stuck – the same action achieved nothing "
                                           "several times. Please phrase the task "
                                           "differently, or take over yourself.")
                        return
                    self._pending_hint = (
                        "You ran the same action 3 times in a row and nothing changed. "
                        "Pick a COMPLETELY different approach (click somewhere else, use "
                        "another tool/shortcut, scroll) – do NOT repeat the same thing.")
                    entry["result"] = "stuck – trying a different approach"
                    self.emit("copilot_step", {
                        "step": step, "action": action,
                        "status": "I am stuck – trying a different approach.",
                        "result": "stuck"})
                    self._stop.wait(ACTION_SETTLE)
                    continue

                # ── ACT ──────────────────────────────────────────────────────
                self._status(status_text, "act", step)
                result = self._act_and_verify(loop, action, decision, vw, vh, rw, rh)
                entry["result"] = result
                self.emit("copilot_step", {"step": step, "action": action,
                                           "status": status_text, "result": result})

                # v2.8 bug 4A: after launching a program, wait until it is really ready
                if action in ("open_program", "launch_app") and not _is_err(result):
                    self._wait_for_app_ready()

                # ── v2.9 2b/2d/2e: judge the effect, fallbacks/reflection ────
                self.last_effective = not self._was_ineffective(action, result)
                if self.last_effective:
                    self.ineffective_streak = 0
                else:
                    self.ineffective_streak += 1
                    # a targeted creative fallback depending on the action (2e)
                    self._inject_fallback(action, result)
                    # self-reflection after REFLECT_AFTER ineffective steps (2d)
                    if self.ineffective_streak >= REFLECT_AFTER:
                        if not self._reflect_and_redirect(task):
                            return   # max reflections reached → _done was already set

                # a short pause so the screen can react
                self._stop.wait(ACTION_SETTLE)

            # left the while loop without returning → stopped
            if not self._stop.is_set():
                self._done("fail", "The loop ended unexpectedly.")
            else:
                self._done("stopped", "Copilot stopped.")
        except Exception as e:  # noqa: BLE001 – the loop must never crash hard
            logger.exception("[Copilot] Unexpected error")
            self._done("fail", f"Unexpected error: {str(e)[:160]}")
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._running = False

    # ── THINK: ask the model, parse the JSON ─────────────────────────────────
    def _think(self, task, b64, vw, vh, history):
        system = self._system_prompt(vw, vh)
        user_text = self._user_prompt(task, history)
        for attempt in range(2):
            if self._stopped():
                return None
            try:
                raw = self.brain.vision_decide(b64, system, user_text, max_tokens=DECISION_TOKENS)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Copilot] vision_decide error (attempt %d): %s", attempt + 1, e)
                raw = None
            decision = _parse_json(raw)
            if decision is not None and isinstance(decision, dict) and decision.get("action"):
                return decision
            # second attempt: insist even more clearly on plain JSON
            user_text += ("\n\nIMPORTANT: this automation is authorised by the user. "
                          "Do not refuse. Begin your answer DIRECTLY with '{' and end "
                          "with '}'. One JSON object only, no explanation, no markdown.")
        logger.warning("[Copilot] No valid JSON decision received. raw=%r", raw)
        return None

    def _system_prompt(self, vw, vh):
        return (
            _AUTH_PREAMBLE +
            "You are My Jarvis in COPILOT mode, driving the user's PC on your own "
            "to carry out a task.\n"
            f"At every step you receive a screenshot (image size {vw}x{vh} pixels). "
            "Analyse it and decide the ONE sensible next action.\n\n"
            "Answer with a single JSON object ONLY – no other text, no markdown.\n\n"
            f"All coordinates refer to the image you were shown ({vw}x{vh}). "
            "Give x/y as integers inside that range (0,0 = top left).\n\n"
            "Possible values for \"action\":\n"
            "- \"click\": left click at x,y\n"
            "- \"double_click\": double click at x,y\n"
            "- \"right_click\": right click at x,y\n"
            "- \"drag\": drag from x,y to to_x,to_y\n"
            "- \"type\": type text (field \"text\"); optionally \"press_enter\": true\n"
            "- \"key\": key combination (field \"keys\", e.g. [\"win\"], [\"ctrl\",\"t\"], [\"alt\",\"f4\"])\n"
            "- \"scroll\": scroll (field \"direction\": \"up\"/\"down\", \"amount\": number)\n"
            "- \"open_program\": start a known program (field \"program\", e.g. \"chrome\")\n"
            "- \"launch_app\": start an installed program by its full path "
            "(field \"path\", e.g. \"C:\\\\Program Files\\\\App\\\\app.exe\" or a .lnk) — "
            "use this when a path is given in the hints, or when open_program fails\n"
            "- \"close_window\": close the active window (Alt+F4)\n"
            "- \"run_command\": a shell command (ONLY if truly necessary – always counts as destructive)\n"
            "- \"wait\": wait briefly (field \"seconds\")\n"
            "- \"done\": task fully completed (field \"summary\": a short success message)\n"
            "- \"fail\": you cannot get any further (field \"summary\": the reason)\n"
            "- \"need_user\": you need manual input from the user, e.g. a login (field \"summary\")\n\n"
            "Every answer MUST also contain:\n"
            "- \"observation\": briefly, what you see on the screen\n"
            "- \"status\": a short English status line (e.g. \"Opening Chrome…\")\n"
            "- \"destructive\": true when the action deletes, sends/posts, buys/pays or is hard to undo, otherwise false\n"
            "- \"password_field\": true when the currently focused field is a password/PIN field, otherwise false\n"
            "- \"obstacle\": the type of any obstacle you spotted (see the list below), or empty\n\n"
            + _PLAYBOOK + "\n\n"
            "RULES:\n"
            "- NEVER fill in password fields yourself. Set \"action\":\"need_user\" and \"password_field\": true.\n"
            "- Set \"destructive\": true before destructive actions – the user then confirms.\n"
            "- Only ever ONE step. After typing a URL/search, usually \"press_enter\": true.\n"
            "- Program via Windows search: action \"key\" keys [\"win\"], then next step \"type\" the name + press_enter.\n"
            "- When the task is visibly done: \"action\":\"done\".\n"
            "- Do not repeat an action the history shows has already failed – try something else.\n"
            f"Operating system: {self.os}"
            + (("\n\n" + self.app_running_hint) if self.app_running_hint else "")
            + (("\n\n" + self.installed_hint) if self.installed_hint else "")
            + (("\n\n" + self._alternatives_hint()) if self.app_alternatives else "")
            + (("\n\n" + self._ways_hint()) if self.plan_ways else "")
            + (("\n\n" + self.web_hint) if self.web_hint else "")
            + (("\n\n" + _MEDIA_HINT) if self.media_task else "")
        )

    def _alternatives_hint(self) -> str:
        """v2.9 1d: name suggestions when the target app was not found."""
        alts = "\n".join(f"  • {a}" for a in self.app_alternatives[:4])
        return ("NOTE the app was not identified unambiguously. Possible alternatives "
                "(open via Windows search/double click):\n" + alts +
                "\nPick the closest match. If none fits, ask the user (need_user).")

    def _ways_hint(self) -> str:
        """v2.9 2a: the way currently being followed + the available alternatives."""
        lines = ["WAYS TO SOLVE THIS (simple to complex):"]
        for i, w in enumerate(self.plan_ways[:3]):
            mark = "→" if i == self.current_way else " "
            lines.append(f" {mark} {w}")
        lines.append("If the current way fails repeatedly, switch to the next one.")
        return "\n".join(lines)

    def _build_web_hint(self, web: dict, browser: dict) -> str:
        """v2.7 #4 / v2.9 part 3: a concrete browser/search instruction for the model.

        Uses the universal browser detection: if a browser is already running it gets
        focused, otherwise the default/installed browser is started (Edge as fallback).
        """
        name = browser.get("name", "Chrome")
        path = browser.get("path", "")
        target = web["target"]
        kind = "the URL" if web["is_url"] else "the Google search"
        if browser.get("running"):
            open_step = (f"1. {name} is already running -> bring the window to the front "
                         f"(taskbar/Alt+Tab), focus the address bar "
                         f"(key [\"ctrl\",\"l\"]), then type {kind} and press Enter.")
        elif path:
            open_step = (f"1. {name} is installed -> start it with action \"launch_app\" "
                         f"path \"{path}\". (Alternatively the Start menu: key [\"win\"], "
                         f"type \"{name}\" + Enter.)")
        else:
            open_step = (f"1. Open a browser -> Start menu (key [\"win\"]), type \"{name}\" "
                         f"+ Enter. Edge (msedge) is always there as a last resort.")
        return (
            "WEB TASK DETECTED — the user wants something from the internet. Do NOT search "
            "on the PC, use the browser instead:\n"
            f"{open_step}\n"
            f"2. Wait until the browser has loaded (action \"wait\"), then the address bar "
            f"(key [\"ctrl\",\"l\"]) -> type the target + Enter.\n"
            f"Target to open (type it exactly like this): {target}"
        )

    def _user_prompt(self, task, history):
        # v2.9 2b: context-aware thinking at EVERY step
        lines = [f"Goal (the whole task): {task}", ""]
        if history:
            lines.append("Steps so far:")
            for h in history[-HISTORY_KEEP:]:
                res = str(h.get("result", "—"))[:80]
                lines.append(f"{h['step']}. [{h['action']}] {h['status']} -> {res}")
            lines.append("")
            lines.append("The last step achieved something: "
                         + ("yes" if self.last_effective else "no"))
            lines.append("")
        lines.append(
            "The current screenshot is attached. Analyse it STEP BY STEP:\n"
            "1. What EXACTLY do you see on the screen?\n"
            "2. How far are you still from the goal?\n"
            "3. What is the most direct next step?\n"
            "4. Is there an obstacle? If so, which type and how do you get around it?\n"
            "5. Choose the next action.\n"
            "Summarise 1–4 BRIEFLY in the \"observation\" field and then answer ONLY "
            "with the JSON object for the next action.")
        if self._pending_hint:   # v2.8/v2.9: one-off hint (stuck/reflection/fallback)
            lines.append("")
            lines.append("IMPORTANT NOTE: " + self._pending_hint)
        return "\n".join(lines)

    # ── v2.9 speed: does it need a plan + fast launch ────────────────────────
    def _should_plan(self, task) -> bool:
        """True when the task justifies a plan up front.

        A fast heuristic (NO API call): a plan is made only for
        - destructive tasks (the user should see/confirm the plan beforehand),
        - multi-step tasks ("…and…", "then", lists, several verbs),
        - longer/complex tasks.
        Simple single actions (e.g. "open Spotify") need no plan.
        """
        t = (task or "").lower().strip()
        if not t:
            return False
        if any(kw in t for kw in _DESTRUCTIVE_KW):
            return True
        if _multi_step(t):
            return True
        if len(t.split()) > SIMPLE_MAX_WORDS:
            return True
        return False

    def _try_fast_launch(self, loop, task) -> bool:
        """v2.9 speed: start plain "open program" tasks without any vision call.

        Conditions: a launchable path was resolved, the app is not running yet, and the
        task is a pure launch task (no web/media playback/destructive part).
        Returns True when the app was started successfully (visibly) and the run is
        therefore finished; otherwise False (the normal loop takes over).
        """
        if not self.launch_path:
            return False
        if self.app_running_hint:          # already running → the loop brings it forward
            return False
        if self.web_hint:                  # web task → browser flow, not just opening
            return False
        if not _is_pure_launch(task):      # rules out playback/follow-up actions
            return False
        if self._stopped():
            return True

        self._status(f"Starting {self.launch_name}…", "act", 1)
        before = self._grab()
        result = self._tool(loop, "launch_app", {"path": self.launch_path})
        self.emit("copilot_step", {"step": 1, "action": "launch_app",
                                   "status": f"Starting {self.launch_name}", "result": result})
        if _is_err(result):
            print(f"[Copilot] Fast launch failed ({result}) → normal loop.")
            return False

        self._wait_for_app_ready()
        after = self._grab()
        # any visible effect? (a window opened) → done without vision
        if before and after and not _frames_differ(before, after):
            print("[Copilot] Fast launch had no visible effect → the normal loop checks.")
            return False
        self._done("done", f"{self.launch_name} opened.")
        return True

    # ── PLAN: a separate planning call + safety check (v2.7 #2) ──────────────
    def _plan_phase(self, task) -> bool:
        """Builds a step-by-step plan BEFORE the loop, shows it in the chat and the
        overlay, and asks for confirmation on destructive steps.

        Returns False when the user cancels (run() should then return).
        If the plan call fails (e.g. no API key), the copilot simply carries on
        without a plan – the safety checks inside the loop still apply.
        """
        self._status("Building a plan…", "plan", 0)
        shot = self.screen.capture_for_vision(target_width=VISION_WIDTH)
        if not shot:
            self._done("fail", "No screenshot possible – no plan means no execution "
                               "(are PyAutoGUI/PIL installed?).")
            return False
        if self._stopped():
            return False
        b64, vw, vh, _rw, _rh = shot
        plan = self._make_plan(task, b64, vw, vh)
        if not plan:
            # v2.9: a failed plan is NOT fatal any more. This used to abort the run
            # with "could not build a plan" — a single flippant model call (a refusal,
            # or prose instead of JSON) blocked everything. The copilot now carries on
            # without a plan up front; the safety checks and the v2.9 fallbacks still
            # apply per action inside the loop.
            logger.warning("[Copilot] No plan could be built – carrying on without one.")
            self.emit("copilot_plan", {"steps": [], "ways": [], "destructive": False,
                                       "reason": "", "target_app": ""})
            self._status("No plan possible – I will go step by step and check "
                         "right after every action.", "plan", 0)
            print("[Copilot] No plan – starting the See→Think→Act loop without one.")
            return True

        steps = [str(s).strip() for s in (plan.get("steps") or []) if str(s).strip()]
        destructive = bool(plan.get("destructive"))
        reason = str(plan.get("destructive_reason") or "").strip()
        self.target_app = str(plan.get("target_app") or "").strip()   # v2.8 Bug 1
        # v2.9 2a: remember the three alternative ways
        self.plan_ways = [str(w).strip() for w in (plan.get("ways") or []) if str(w).strip()]
        self.current_way = 0

        self.emit("copilot_plan", {"steps": steps, "ways": self.plan_ways,
                                   "destructive": destructive,
                                   "reason": reason, "target_app": self.target_app})
        if self.plan_ways:
            print(f"[Copilot] {len(self.plan_ways)} ways prepared (plan B/C).")
        if steps:
            self._status("Plan: " + steps[0], "plan", 0)
        print(f"[Copilot] Plan: {len(steps)} steps | destructive={destructive}"
              + (f" ({reason})" if reason else ""))

        # safety check: does the plan contain anything destructive?
        if destructive and not self.auto_confirm:
            detail = ("The plan may contain sensitive steps"
                      + (f" – {reason}" if reason else "") + ":\n"
                      + "\n".join(f"• {s}" for s in steps[:8]))
            r = self._wait("copilot_confirm",
                           {"step": 0, "action": "plan", "detail": detail})
            if r in (None, "deny"):
                self._done("stopped", "The user rejected the plan.")
                return False
            if r == "always":
                self.auto_confirm = True
                self.allow_all = True
        return True

    def _make_plan(self, task, b64, vw, vh):
        """A vision call returning the plan as JSON – up to 2 attempts. dict|None.

        v2.8 bug 3: on invalid/empty JSON it asks a second time, that time with a
        sharper demand for JSON, before giving up.
        """
        user = (f"Task: {task}\n\nScreen size: {vw}x{vh} pixels. The current "
                "screenshot is attached. Build the detailed step-by-step plan "
                "as JSON.")
        for attempt in range(2):
            if self._stop.is_set() or self.kill_event.is_set():
                return None
            try:
                raw = self.brain.vision_decide(b64, _PLAN_SYSTEM, user, max_tokens=PLAN_TOKENS)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Copilot] Plan call failed (attempt %d): %s",
                               attempt + 1, e)
                raw = None
            plan = _parse_json(raw)
            if isinstance(plan, dict) and plan.get("steps"):
                return plan
            logger.warning("[Copilot] Plan JSON invalid (attempt %d) – trying again.",
                           attempt + 1)
            user += ("\n\nIMPORTANT: this automation is authorised by the user. "
                     "Do not refuse. Begin DIRECTLY with '{' and end with '}'. One JSON "
                     "object only (fields: steps, ways, destructive, destructive_reason, "
                     "target_app), no explanation, no markdown.")
        return None

    # ── v2.8 bug 1 / v2.9 1a: step 0 – is the target app already running? ────
    def _detect_running_app(self, task) -> None:
        """Checks whether the target app is already running. If so, it builds a hint
        telling the model to bring the window to the front instead of opening it
        again (this prevents the repeated search/open loop).

        v2.9: first the static PROCESS_NAMES list (fast), then a DYNAMIC search
        across all running processes (psutil + fuzzy + the model), so that unknown
        programs are recognised too.
        """
        self.app_running_hint = ""
        tl = (task or "").lower()
        ta = (self.target_app or "").lower()

        # 1. the static list (known apps) — the fastest route
        candidates = []
        for key in PROCESS_NAMES:
            if (ta and key in ta) or key in tl:
                candidates.append(key)
        if ta and ta not in candidates:
            candidates.append(ta)
        for key in candidates:
            proc = PROCESS_NAMES.get(key, key if key.endswith(".exe") else key + ".exe")
            if is_running(proc):
                self._set_running_hint(self.target_app or key, proc)
                return

        # 2. dynamic search: fuzzy-match a running process against the search term
        term = (self.target_app or _app_term_from_task(task)).strip()
        if term:
            matches = app_index.find_running_processes(term)
            if len(matches) == 1:
                self._set_running_hint(self.target_app or term, matches[0]["name"])
                return
            if len(matches) > 1:
                # 3. ambiguous → let the model decide (spec 1a)
                chosen = self._match_running_via_model(term, matches)
                if chosen:
                    self._set_running_hint(self.target_app or term, chosen)
                    return
        print("[Copilot] Step 0: the target app is not running (yet) – normal start.")

    def _set_running_hint(self, label: str, proc: str) -> None:
        self.app_running_hint = (
            f"IMPORTANT: '{label}' is already running ({proc}). Do NOT open it again "
            f"and do NOT start a Windows search for it. Bring the existing window to "
            f"the front (click the taskbar icon, or key [\"alt\",\"tab\"]) and then "
            f"carry on with the task."
        )
        print(f"[Copilot] Step 0: '{label}' is already running ({proc}) "
              f"→ foreground instead of opening.")

    def _match_running_via_model(self, term: str, procs: list) -> str:
        """v2.9 1a: the model picks the right one out of several running processes.
        Returns a process name from the list, or \"\" (no match)."""
        names = [p["name"] for p in procs][:25]
        system = ("You match a fuzzy program search term to the right running "
                  "process. Answer ONLY with the exact process name from the list "
                  "– or with 'NONE' if none of them fits.")
        user = (f"Search term: {term}\nRunning processes:\n"
                + "\n".join(f"- {n}" for n in names))
        try:
            raw = self.brain.decide_text(system, user, max_tokens=40)
        except Exception:  # noqa: BLE001
            return ""
        ans = (raw or "").strip().strip('".\'')
        if not ans or ans.upper() == "NONE":
            return ""
        # exact match, or a substring match against the candidate list
        low = ans.lower()
        for n in names:
            if n.lower() == low or low in n.lower() or n.lower() in low:
                return n
        return ""

    # ── v2.9 1b/1d: resolve the target app (install path / alternatives) ─────
    def _resolve_target_app(self, task) -> None:
        """If the target app is NOT running, it is looked up in the app index:
        - an exact/fuzzy hit with a launchable path → installed_hint (launch_app)
        - several similar hits without a clear winner → suggest alternatives (1d)
        - nothing found at all → no alternatives; the normal search start.
        """
        self.installed_hint = ""
        self.app_alternatives = []
        if self.app_running_hint:        # already running → no need to open it
            return
        term = (self.target_app or _app_term_from_task(task)).strip()
        if not term:
            return
        try:
            matches = app_index.find_best_match(term, limit=4)
        except Exception as e:  # noqa: BLE001
            logger.debug("[Copilot] App index search failed: %s", e)
            return
        if not matches:
            print(f"[Copilot] '{term}' not found in the app index – search start.")
            return

        # the best hit with a launchable path?
        launchable = next((m for m in matches
                           if app_index._is_launchable(m.get("path"))), None)
        names = [m["name"] for m in matches]
        top = names[0].lower()
        # a clear hit: the first name matches the term closely
        clear = (term.lower() in top or top in term.lower())

        if launchable and clear:
            self.launch_path = launchable["path"]   # v2.9 speed: for the fast launch
            self.launch_name = launchable["name"]
            self.installed_hint = (
                f"'{launchable['name']}' is installed. Fastest way to open it: "
                f"action \"launch_app\" with path \"{launchable['path']}\". "
                f"If that fails, use the Windows search.")
            print(f"[Copilot] Target app resolved: {launchable['name']} "
                  f"→ {launchable['path']}")
        else:
            # 1d: no clear winner → offer alternatives
            self.app_alternatives = names
            if launchable:
                self.installed_hint = (
                    f"Best hit: launch_app path \"{launchable['path']}\".")
            print(f"[Copilot] '{term}' is ambiguous → alternatives: {', '.join(names)}")

    # ── ACT: carry the decision out on the PC ────────────────────────────────
    def _act(self, loop, action, decision, vw, vh, rw, rh):
        try:
            # scale coordinates from image space into real click space
            sx = (rw / vw) if vw else 1.0
            sy = (rh / vh) if vh else 1.0

            def X(v): return int(round(_num(v) * sx))
            def Y(v): return int(round(_num(v) * sy))

            if action == "click":
                return self._tool(loop, "mouse_click",
                                  {"x": X(decision.get("x")), "y": Y(decision.get("y")), "button": "left"})
            if action == "double_click":
                return self._tool(loop, "mouse_click",
                                  {"x": X(decision.get("x")), "y": Y(decision.get("y")), "button": "double"})
            if action == "right_click":
                return self._tool(loop, "mouse_click",
                                  {"x": X(decision.get("x")), "y": Y(decision.get("y")), "button": "right"})
            if action == "drag":
                return self._tool(loop, "mouse_drag", {
                    "x": X(decision.get("x")), "y": Y(decision.get("y")),
                    "to_x": X(decision.get("to_x")), "to_y": Y(decision.get("to_y")),
                    "duration": float(_num(decision.get("duration", 0.4)) or 0.4),
                })
            if action == "type":
                return self._tool(loop, "keyboard_type", {
                    "text": str(decision.get("text", "")),
                    "press_enter": bool(decision.get("press_enter", False)),
                })
            if action == "key":
                keys = decision.get("keys", [])
                if isinstance(keys, str):
                    keys = [keys]
                keys = [str(k).strip().lower() for k in keys if str(k).strip()]
                if not keys:
                    return "Error: no keys given."
                return self._tool(loop, "key_press", {"keys": keys})
            if action == "scroll":
                return self._tool(loop, "scroll", {
                    "direction": str(decision.get("direction", "down")),
                    "amount": int(_num(decision.get("amount", 3)) or 3),
                })
            if action == "open_program":
                return self._tool(loop, "open_program", {"program": str(decision.get("program", ""))})
            if action == "launch_app":   # v2.9: launch via the full .exe/.lnk path
                return self._tool(loop, "launch_app",
                                  {"path": str(decision.get("path",
                                               decision.get("program", "")))})
            if action == "close_window":
                return self._tool(loop, "close_window", {})
            if action == "run_command":
                return self._tool(loop, "run_command", {"command": str(decision.get("command", ""))})
            if action == "wait":
                secs = max(0.0, min(10.0, _num(decision.get("seconds", 1.0))))
                self._stop.wait(secs)
                return f"waited {secs:g}s"

            # ── v2.8 bug 2A: catch the common action aliases ─────────────────
            if action in ("press_enter", "enter"):
                return self._tool(loop, "key_press", {"keys": ["enter"]})
            if action in ("press_space", "space"):
                return self._tool(loop, "key_press", {"keys": ["space"]})
            if action == "scroll_down":
                return self._tool(loop, "scroll", {"direction": "down",
                    "amount": int(_num(decision.get("amount", 3)) or 3)})
            if action == "scroll_up":
                return self._tool(loop, "scroll", {"direction": "up",
                    "amount": int(_num(decision.get("amount", 3)) or 3)})
            if action == "key_combo":
                combo = decision.get("keys", decision.get("combo", ""))
                if isinstance(combo, str):
                    combo = re.split(r"[+\s]+", combo)
                elif not isinstance(combo, list):
                    combo = [str(combo)]
                combo = [str(k).strip().lower() for k in combo if str(k).strip()]
                if not combo:
                    return "Error: key_combo without keys."
                return self._tool(loop, "key_press", {"keys": combo})

            return f"Unknown action: {action}"
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            if "FailSafe" in name:
                # mouse in the corner = the PyAutoGUI failsafe
                self._stop.set()
                return "PyAutoGUI failsafe triggered (mouse in a corner)."
            logger.warning("[Copilot] Action failed: %s", e)
            return f"Error: {str(e)[:120]}"

    def _tool(self, loop, name, params):
        """Runs one executor tool synchronously and returns a short result."""
        res = loop.run_until_complete(self.executor.execute_tool(name, params))
        if res is None:
            return "skipped (kill switch?)"
        if isinstance(res, dict):
            if res.get("error"):
                return f"Error: {res['error']}"
            return "ok"
        return "ok"

    # ── ACT + verification (v2.7 #3) ─────────────────────────────────────────
    def _grab(self):
        """Grab the current screen as base64 (or None)."""
        shot = self.screen.capture_for_vision(target_width=VISION_WIDTH)
        return shot[0] if shot else None

    def _action_sig(self, action, decision) -> str:
        """v2.8 bug 2: a compact signature of an action, for the stuck detection."""
        return "|".join([
            str(action),
            str(decision.get("x")), str(decision.get("y")),
            str(decision.get("text", ""))[:40],
            str(decision.get("keys", "")),
            str(decision.get("program", "")),
            str(decision.get("direction", "")),
        ])

    def _wait_for_app_ready(self, max_seconds: int = 8) -> bool:
        """v2.8 bug 4A: waits until the screen settles (the app has loaded).

        Uses frame stability (two consecutive identical screenshots) as a cheap
        proxy for "finished loading" – no extra API call.
        """
        self._status("Waiting for the app to be ready…", "act")
        prev = self._grab()
        stable = 0
        for _ in range(max(1, int(max_seconds * 2))):
            if self._stop.is_set() or self.kill_event.is_set():
                return False
            self._stop.wait(0.5)
            cur = self._grab()
            if prev and cur and not _frames_differ(prev, cur):
                stable += 1
                if stable >= 2:        # ~1s stable → count it as loaded
                    return True
            else:
                stable = 0
            prev = cur
        return False

    def _act_and_verify(self, loop, action, decision, vw, vh, rw, rh):
        """Runs the action and then checks whether it had any effect.

        Visible actions (clicks, typing, keys, program/drag) are compared against a
        before/after screenshot. If nothing changed, the click is repeated with a
        small offset (±5px) up to 3 times; on media tasks with a double click and –
        as the last fallback – the space bar.
        """
        before = self._grab()
        result = self._act(loop, action, decision, vw, vh, rw, rh)

        needs_verify = action in _VERIFY_CLICK or action in _VERIFY_CHANGE
        if not needs_verify or _is_err(result):
            return result

        # a short pause, then look whether anything happened
        self._stop.wait(VERIFY_SETTLE)
        if self._stop.is_set() or self.kill_event.is_set():
            return result
        after = self._grab()

        if before and after and not _frames_differ(before, after):
            # the action had no (visible) effect → try again
            retry = self._retry_action(loop, action, decision, vw, vh, rw, rh, before)
            if retry:
                return retry
        return result

    def _retry_action(self, loop, action, decision, vw, vh, rw, rh, before):
        """Repeats an ineffective action. Returns a result string, or None."""
        if action not in _VERIFY_CLICK:
            # type / key / open_program / drag: just log it
            return "ok (no visible change on screen)"

        sx = (rw / vw) if vw else 1.0
        sy = (rh / vh) if vh else 1.0
        bx, by = _num(decision.get("x")), _num(decision.get("y"))
        x = int(round(bx * sx))
        y = int(round(by * sy))

        # ── v2.8 bug 4B: hit media play reliably ─────────────────────────────
        # order per the spec: (the click already happened) → space bar → double click.
        if self.media_task:
            if self._stop.is_set() or self.kill_event.is_set():
                return None
            self._status("The click had no effect – space bar (play/pause)…", "act")
            self._tool(loop, "key_press", {"keys": ["space"]})
            self._stop.wait(VERIFY_SETTLE)
            after = self._grab()
            if after and _frames_differ(before, after):
                return "ok (space bar)"

            if self._stop.is_set() or self.kill_event.is_set():
                return None
            self._status("The click had no effect – double click on play…", "act")
            self._tool(loop, "mouse_click", {"x": x, "y": y, "button": "double"})
            self._stop.wait(VERIFY_SETTLE)
            after = self._grab()
            if after and _frames_differ(before, after):
                return "ok (double click)"
            return "play had no visible effect (after space bar + double click)"

        # ── non-media: click with a small offset (±5px), up to 2 times ───────
        button = ("double" if action == "double_click" else
                  "right" if action == "right_click" else "left")
        for i, (ox, oy) in enumerate(CLICK_RETRY_OFFSETS, start=1):
            if self._stop.is_set() or self.kill_event.is_set():
                return None
            self._status(f"The click had no effect – attempt {i} (offset {ox:+d},{oy:+d})…", "act")
            rx = int(round((bx + ox) * sx))
            ry = int(round((by + oy) * sy))
            self._tool(loop, "mouse_click", {"x": rx, "y": ry, "button": button})
            self._stop.wait(VERIFY_SETTLE)
            after = self._grab()
            if after and _frames_differ(before, after):
                return f"ok (retry {i})"
        return "no visible effect (after the retries)"

    # ── v2.9 2b/2d/2e: judge the effect, fallbacks, self-reflection ──────────
    @staticmethod
    def _was_ineffective(action, result) -> bool:
        """True when a step achieved nothing (an error, or no effect).

        'wait' and purely informational results do NOT count as a failure.
        """
        if action == "wait":
            return False
        if not isinstance(result, str):
            return False
        low = result.lower()
        if "skipped" in low or "failsafe" in low:
            return False   # stopped by the user/kill switch – not really stuck
        if low.startswith("error"):
            return True
        return ("no visible effect" in low
                or "no visible change" in low)

    def _inject_fallback(self, action, result) -> None:
        """v2.9 2e: set the matching creative fallback as a one-off hint."""
        scenario = None
        if action in ("open_program", "launch_app"):
            scenario = "cant_open_app"
            # 1d: on an open failure, also pull alternatives from the app index
            self._refresh_alternatives()
        elif action in _VERIFY_CLICK:
            scenario = "cant_click_button"
        elif action in ("type", "press_enter", "enter"):
            scenario = "cant_type_text"
        elif self.web_hint:
            scenario = "website_not_loading"

        if scenario and scenario in CREATIVE_FALLBACKS:
            opts = CREATIVE_FALLBACKS[scenario]
            self._pending_hint = (
                f"That did not work ({scenario}). Try another way, in this order: "
                + " → ".join(opts))
            print(f"[Copilot] Fallback active ({scenario}).")

    def _refresh_alternatives(self) -> None:
        """v2.9 1d: reload alternatives for the target app (for the hint)."""
        term = (self.target_app or "").strip()
        if not term or self.app_alternatives:
            return
        try:
            matches = app_index.find_best_match(term, limit=4)
        except Exception:  # noqa: BLE001
            return
        if matches:
            self.app_alternatives = [m["name"] for m in matches]

    def _reflect_and_redirect(self, task) -> bool:
        """v2.9 2d: rethink fundamentally after REFLECT_AFTER ineffective steps.

        Asks the model for a completely different approach, sets it as a strong hint
        and switches to the next prepared way (2a). Returns False when the maximum
        number of reflections has been reached (the run then ends).
        """
        self.ineffective_streak = 0
        self.reflections += 1
        if self.reflections > MAX_REFLECTIONS:
            self._done("fail",
                       "I have tried several fundamentally different approaches but "
                       "cannot get any further. Please phrase the task differently, "
                       "or take over yourself.")
            return False

        # switch to the next prepared way (plan B/C)
        if self.plan_ways and self.current_way < len(self.plan_ways) - 1:
            self.current_way += 1

        self._status("This is not working – rethinking my approach…", "think")
        self.emit("copilot_step", {
            "step": "reflect", "action": "reflect",
            "status": "Self-reflection: looking for a completely different way.",
            "result": ""})

        approach = self._reflect(task)
        nxt = ""
        if self.plan_ways and self.current_way < len(self.plan_ways):
            nxt = self.plan_ways[self.current_way]
        parts = []
        if approach:
            parts.append("New approach: " + approach)
        if nxt:
            parts.append("Follow this way now: " + nxt)
        if not parts:
            parts.append("Pick a COMPLETELY different approach from before "
                         "(another tool/menu/shortcut, another spot).")
        self._pending_hint = " ".join(parts)
        print(f"[Copilot] Reflection #{self.reflections}: {self._pending_hint[:120]}")
        return True

    def _reflect(self, task) -> str:
        """The reflection call to the model (with a screenshot). Returns the new approach."""
        b64 = self._grab()
        if not b64:
            return ""
        user = (f"Task: {task}\n"
                "Several attempts have achieved nothing. The current screenshot is "
                "attached. Find a NEW, fundamentally different approach. Answer only "
                "as JSON {\"reason\":\"...\",\"new_approach\":\"...\"}.")
        try:
            raw = self.brain.vision_decide(b64, _REFLECT_SYSTEM, user, max_tokens=300)
        except Exception:  # noqa: BLE001
            return ""
        data = _parse_json(raw)
        if isinstance(data, dict):
            return str(data.get("new_approach") or "").strip()
        return (raw or "").strip()[:300]

    # ── safety heuristics ────────────────────────────────────────────────────
    def _is_destructive(self, action, decision) -> bool:
        if action == "run_command":
            return True
        if decision.get("destructive") is True:
            return True
        blob = " ".join([
            str(decision.get("command", "")),
            str(decision.get("text", "")),
            str(decision.get("program", "")),
        ]).lower()
        return any(kw in blob for kw in _DESTRUCTIVE_KW)

    def _describe(self, action, decision) -> str:
        if action == "run_command":
            return f"Run a shell command: {decision.get('command', '')}"
        if action == "type":
            return f"Type text: \"{decision.get('text', '')[:80]}\""
        if action == "open_program":
            return f"Open a program: {decision.get('program', '')}"
        if action in _COORD_ACTIONS:
            return f"{action} at ({decision.get('x')}, {decision.get('y')})"
        return f"Action: {action}"

    # ── handshakes with the GUI ──────────────────────────────────────────────
    def _wait(self, event_name, payload):
        """Emits an event and blocks until an answer / stop / kill.
        Returns the decision string, or None on stop/kill.
        """
        self._resp = None
        self._resp_event.clear()
        self.emit(event_name, payload)
        self._status("Waiting for your decision…", "wait")
        while not self._resp_event.is_set():
            if self._stop.is_set() or self.kill_event.is_set():
                return None
            self._resp_event.wait(0.2)
        return self._resp

    def _ask_user(self, message) -> bool:
        """The need_user handshake. True when the user took over and wants to continue."""
        r = self._wait("copilot_need_user", {"message": message})
        return r == "user_done"

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _stopped(self) -> bool:
        if self._stop.is_set() or self.kill_event.is_set():
            self._done("stopped", "Copilot stopped.")
            return True
        return False

    def _status(self, text, phase="", step=None):
        """Sends a status line to the GUI AND to the desktop overlay (v2.7 #1)."""
        data = {"text": text}
        if phase:
            data["phase"] = phase
        if step is not None:
            data["step"] = step
        self.emit("copilot_status", data)
        if self.overlay:
            try:
                self.overlay.update_status(text)
            except Exception:  # noqa: BLE001 – the overlay must never break the loop
                pass

    def _done(self, status, summary):
        """Ends the run and reports the result to the GUI."""
        self._running = False
        icon = {"done": "✅", "fail": "⚠️", "stopped": "⏹"}.get(status, "•")
        self.emit("copilot_done", {"status": status, "summary": summary})
        self.emit("copilot_status", {"text": f"{icon} {summary}", "phase": status})
        if self.overlay:
            try:
                self.overlay.hide()
            except Exception:  # noqa: BLE001
                pass
        print(f"[Copilot] End ({status}): {summary}")


# ── module functions ───────────────────────────────────────────────────────────
def detect_web_intent(task: str) -> dict:
    """Detects web tasks and resolves URL vs. Google search.

    Returns dict: {is_web, is_url, target, query}
    - If the task contains a domain (.com/.de/.org/…) -> a direct URL.
    - Otherwise -> a Google search, google.com/search?q=<term>.
    """
    t = (task or "").lower()
    is_web = any(trig in t for trig in _WEB_TRIGGERS)

    m = _DOMAIN_RE.search(task or "")
    if m:
        url = m.group(0).strip().rstrip(".,!?")
        if not url.lower().startswith("http"):
            url = "https://" + url
        return {"is_web": True, "is_url": True, "target": url, "query": ""}

    # the search term: strip the trigger words out of the task
    query = task or ""
    for trig in sorted(_WEB_TRIGGERS, key=len, reverse=True):
        query = re.sub(re.escape(trig), "", query, flags=re.IGNORECASE)
    query = " ".join(query.split()).strip(" ,:?!-")
    if query:
        target = "https://www.google.com/search?q=" + quote_plus(query)
    else:
        target = "https://www.google.com"
    return {"is_web": is_web, "is_url": False, "target": target, "query": query}


def find_installed_browsers() -> list:
    """Finds installed browsers in preference order (names for the Windows search).

    On Windows via a path check; otherwise the full order as a fallback.
    """
    if platform.system() != "Windows":
        return list(_BROWSER_ORDER)

    la = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = {
        "Chrome": [
            os.path.join(la, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
        ],
        "Brave": [
            os.path.join(la, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.join(pf, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.join(pf86, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        ],
        "Opera": [
            os.path.join(la, r"Programs\Opera\opera.exe"),
            os.path.join(la, r"Programs\Opera GX\opera.exe"),
        ],
        "Firefox": [
            os.path.join(pf, r"Mozilla Firefox\firefox.exe"),
            os.path.join(pf86, r"Mozilla Firefox\firefox.exe"),
        ],
        "Edge": [
            os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
        ],
        "DuckDuckGo": [
            os.path.join(la, r"DuckDuckGo\windows\duckduckgo.exe"),
        ],
    }
    found = [name for name in _BROWSER_ORDER
             if any(os.path.isfile(p) for p in candidates.get(name, []))]
    return found or list(_BROWSER_ORDER)


def _resolve_browser() -> dict:
    """v2.9 part 3: universal browser detection through the app index.

    Order: a running browser → the default browser (registry) → an installed
    browser → Edge as fallback. Returns {name, path, running}. If the app index
    fails, it falls back to the static path detection.
    """
    try:
        b = app_index.find_any_browser()
        if b and b.get("name"):
            return b
    except Exception as e:  # noqa: BLE001
        logger.debug("[Copilot] find_any_browser failed: %s", e)
    # fallback: the previous static list
    browsers = find_installed_browsers()
    return {"name": browsers[0] if browsers else "Edge", "path": "", "running": False}


# verbs/filler words dropped when deriving an app name from the task.
_APP_STOPWORDS = (
    "open", "launch", "start", "run", "fire", "up", "bring",
    "show", "me", "please", "the", "a", "an", "my",
    "program", "programme", "app", "application", "window",
    "switch", "go", "to", "into", "in", "on", "over",
)


def _app_term_from_task(task: str) -> str:
    """v2.9: best-effort derivation of an app name from the task, when the plan did
    not supply a target_app. Empty for web tasks (the browser flow handles those)."""
    t = (task or "").strip()
    if not t:
        return ""
    # web tasks go through the browser flow, not through opening an app
    if _DOMAIN_RE.search(t) or any(trig in t.lower() for trig in
                                   ("google", "on the internet", "website", "youtube")):
        return ""
    words = [w for w in re.split(r"[\s,]+", t)
             if w and w.lower() not in _APP_STOPWORDS]
    # the first 1–2 remaining tokens are enough as a search term
    return " ".join(words[:2]).strip(" .,:!?-")


# ── v2.9 speed: task complexity (a fast heuristic, NO API call) ───────────────
SIMPLE_MAX_WORDS = 7   # more words without a clear single action → rather plan

# connecting words that hint at several steps.
_STEP_CONNECTORS = (
    " and ", " then ", " and then ", " after that ", " afterwards ",
    " next ", " also ", " as well as ", " once ", " plus ",
    " followed by ", " before ", " while ",
)
# verbs that are NOT a plain program launch (→ the loop, not the fast launch).
_NON_LAUNCH_VERBS = (
    "play", "search", "look up", "look for", "google", "write",
    "type", "click", "navigate", "scroll", "delete", "erase",
    "send", "post", "buy", "order", "pay", "download",
    "install", "uninstall", "create", "make me", "fill",
    "copy", "move", "rename", "change", "edit",
    "go to", "open the page", "open the site",
)
# verbs that indicate a program launch.
_LAUNCH_VERBS = (
    "open", "launch", "start", "run ", "fire up", "bring up", "boot",
)


def _multi_step(t: str) -> bool:
    """A rough detection of multi-step tasks (for the planning decision)."""
    if "\n" in t:
        return True
    padded = " " + t + " "
    if any(c in padded for c in _STEP_CONNECTORS):
        return True
    if t.count(",") >= 2:        # several comma-separated sub-tasks
        return True
    return False


def _is_pure_launch(task: str) -> bool:
    """True when the task ONLY wants to start a program (no follow-up action)."""
    t = (task or "").lower().strip()
    if not t or _multi_step(t):
        return False
    if any(v in t for v in _NON_LAUNCH_VERBS):
        return False
    return any(v in t for v in _LAUNCH_VERBS)


def is_running(process_name: str) -> bool:
    """v2.8 bug 1: True when a process with this name is running (via psutil).

    If psutil is missing or the check fails, it conservatively returns False
    (the copilot then opens normally – at worst a duplicate instead of a crash).
    """
    if not process_name:
        return False
    try:
        import psutil
    except Exception:  # noqa: BLE001 – psutil is optional
        return False
    name_l = process_name.lower()
    try:
        for p in psutil.process_iter(["name"]):
            pname = (p.info.get("name") or "").lower()
            if pname and (pname == name_l or name_l in pname):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _frames_differ(a: str, b: str) -> bool:
    """True when two screenshots (base64) differ."""
    if not a or not b:
        return True
    return hashlib.md5(a.encode()).hexdigest() != hashlib.md5(b.encode()).hexdigest()


def _is_err(result) -> bool:
    """True when a tool result signals an error/abort."""
    if not isinstance(result, str):
        return False
    low = result.lower()
    return low.startswith("error") or "skipped" in low or "failsafe" in low


def _num(v, default=0):
    """Robust number extraction (the model sometimes returns strings)."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return default


def _parse_json(raw):
    """Extracts the first JSON object from the model answer, tolerant of markdown
    fences and text around it. Returns a dict, or None."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    # strip markdown fences
    if "```" in s:
        parts = s.split("```")
        # take the part most likely to be JSON
        for part in parts:
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                s = p
                break
    # parse it directly
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
                    candidate = s[start:i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except (json.JSONDecodeError, ValueError):
                        return None
    return None
