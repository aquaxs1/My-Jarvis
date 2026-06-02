"""
JARVIS Proactive Engine
- Schlägt nächste Schritte vor basierend auf Kontext
- Tageszeit-abhängige Vorschläge
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class ProactiveEngine:
    def __init__(self, config: dict, calendar=None, tasks=None):
        self.config = config
        self.calendar = calendar
        self.tasks = tasks
        self._last_suggestions = []

    @property
    def is_enabled(self) -> bool:
        return self.config.get("proactive_enabled", True)

    def get_suggestions(self, last_action: str = "", last_reply: str = "") -> list:
        if not self.is_enabled:
            return []

        suggestions = []
        hour = datetime.now().hour

        if hour < 10:
            suggestions.append("Gib mir mein Briefing")
        elif hour >= 22:
            suggestions.append("Fasse meinen Tag zusammen")

        if self.calendar and self.calendar.is_configured:
            try:
                events = self.calendar.get_events(days=1)
                if events:
                    next_ev = events[0]
                    suggestions.append(f"Details zu '{next_ev['title']}'")
            except Exception:
                pass

        if self.tasks and self.tasks.is_configured:
            try:
                open_tasks = self.tasks.get_all_tasks()
                if open_tasks:
                    suggestions.append(f"Was sind meine offenen Aufgaben?")
            except Exception:
                pass

        context_suggestions = self._context_based(last_action, last_reply)
        suggestions.extend(context_suggestions)

        unique = []
        seen = set()
        for s in suggestions:
            if s.lower() not in seen:
                unique.append(s)
                seen.add(s.lower())

        self._last_suggestions = unique[:3]
        return self._last_suggestions

    def _context_based(self, action: str, reply: str) -> list:
        suggestions = []
        action_lower = action.lower() if action else ""
        reply_lower = reply.lower() if reply else ""

        if any(kw in action_lower for kw in ["termin", "meeting", "besprechung"]):
            suggestions.append("Soll ich eine Erinnerung setzen?")

        if any(kw in action_lower for kw in ["code", "programm", "script"]):
            suggestions.append("Soll ich den Code testen?")

        if any(kw in reply_lower for kw in ["zusammenfassung", "zusammengefasst"]):
            suggestions.append("Soll ich die Zusammenfassung als Notiz speichern?")

        if any(kw in action_lower for kw in ["email", "e-mail", "mail"]):
            suggestions.append("Soll ich eine Antwort entwerfen?")

        return suggestions
