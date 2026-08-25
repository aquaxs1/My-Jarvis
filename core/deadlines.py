"""
My Jarvis deadline tracker
- collects deadlines from the calendar, tasks and conversations
- warns about approaching deadlines (24h, 1h, 15min)
"""
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

WARN_LEVELS = [
    {"name": "info", "hours": 24, "color": "yellow"},
    {"name": "warning", "hours": 1, "color": "orange"},
    {"name": "critical", "minutes": 15, "color": "red"},
]


class DeadlineTracker:
    def __init__(self, config: dict, memory=None, calendar=None, tasks=None):
        self.config = config
        self.memory = memory
        self.calendar = calendar
        self.tasks = tasks
        self._warned = {}
        self._custom_deadlines = []
        self._stop_event = threading.Event()
        self._thread = None

    def add_deadline(self, title: str, deadline_dt: datetime):
        self._custom_deadlines.append({
            "title": title,
            "deadline": deadline_dt,
            "source": "conversation",
        })
        logger.info("[Deadlines] Neue Deadline: %s um %s", title, deadline_dt)

    def get_all_deadlines(self) -> list:
        deadlines = list(self._custom_deadlines)

        if self.calendar and self.calendar.is_configured:
            try:
                events = self.calendar.get_events(days=2)
                for ev in events:
                    try:
                        dt = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
                        deadlines.append({
                            "title": ev["title"],
                            "deadline": dt.replace(tzinfo=None),
                            "source": "calendar",
                        })
                    except (ValueError, KeyError):
                        pass
            except Exception as e:
                logger.debug("[Deadlines] Kalender-Fehler: %s", e)

        if self.tasks and self.tasks.is_configured:
            try:
                task_list = self.tasks.get_all_tasks()
                for t in task_list:
                    if t.get("due"):
                        try:
                            dt = datetime.fromisoformat(t["due"])
                            deadlines.append({
                                "title": t["content"],
                                "deadline": dt,
                                "source": t.get("source", "tasks"),
                            })
                        except (ValueError, KeyError):
                            pass
            except Exception as e:
                logger.debug("[Deadlines] Tasks-Fehler: %s", e)

        return sorted(deadlines, key=lambda d: d["deadline"])

    def check_warnings(self) -> list:
        now = datetime.now()
        warnings = []

        for dl in self.get_all_deadlines():
            deadline = dl["deadline"]
            remaining = deadline - now

            if remaining.total_seconds() < 0:
                continue

            warn_key = f"{dl['title']}_{dl['deadline']}"

            if remaining <= timedelta(minutes=15):
                level = "critical"
            elif remaining <= timedelta(hours=1):
                level = "warning"
            elif remaining <= timedelta(hours=24):
                level = "info"
            else:
                continue

            already_warned = self._warned.get(warn_key, "")
            if already_warned == level:
                continue

            self._warned[warn_key] = level
            remaining_str = self._format_remaining(remaining)
            warnings.append({
                "title": dl["title"],
                "deadline": deadline.isoformat(),
                "remaining": remaining_str,
                "level": level,
                "source": dl.get("source", ""),
            })

        return warnings

    def start_checker(self, callback):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._checker_loop, args=(callback,), daemon=True)
        self._thread.start()
        logger.info("[Deadlines] Checker gestartet")

    def stop_checker(self):
        self._stop_event.set()

    def _checker_loop(self, callback):
        while not self._stop_event.is_set():
            try:
                warnings = self.check_warnings()
                for w in warnings:
                    callback(w)
            except Exception as e:
                logger.error("[Deadlines] Checker-Fehler: %s", e)
            self._stop_event.wait(60)

    @staticmethod
    def _format_remaining(td: timedelta) -> str:
        total_seconds = int(td.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds} Sekunden"
        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes} Minuten"
        hours = minutes // 60
        remaining_min = minutes % 60
        if hours < 24:
            return f"{hours}h {remaining_min}min" if remaining_min else f"{hours} Stunden"
        days = hours // 24
        return f"{days} Tag(e), {hours % 24}h"
