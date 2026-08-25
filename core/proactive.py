"""
My Jarvis proactive engine
- suggests next steps based on the context
- suggestions that depend on the time of day
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
            suggestions.append("Give me my briefing")
        elif hour >= 22:
            suggestions.append("Sum up my day")

        if self.calendar and self.calendar.is_configured:
            try:
                events = self.calendar.get_events(days=1)
                if events:
                    next_ev = events[0]
                    suggestions.append(f"Details on '{next_ev['title']}'")
            except Exception:
                pass

        if self.tasks and self.tasks.is_configured:
            try:
                open_tasks = self.tasks.get_all_tasks()
                if open_tasks:
                    suggestions.append("What are my open tasks?")
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
            suggestions.append("Should I set a reminder?")

        if any(kw in action_lower for kw in ["code", "programm", "script"]):
            suggestions.append("Should I test the code?")

        if any(kw in reply_lower for kw in ["zusammenfassung", "zusammengefasst"]):
            suggestions.append("Should I save the summary as a note?")

        if any(kw in action_lower for kw in ["email", "e-mail", "mail"]):
            suggestions.append("Should I draft a reply?")

        return suggestions
