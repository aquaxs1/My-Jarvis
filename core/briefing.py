"""
My Jarvis daily briefing
- summarises weather, news, appointments and tasks
- automatically at the configured time, or on request
"""
import logging
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

logger = logging.getLogger(__name__)


class BriefingManager:
    def __init__(self, config: dict, calendar=None, tasks=None):
        self.config = config
        self.calendar = calendar
        self.tasks = tasks
        self._scheduler_thread = None
        self._stop_event = threading.Event()

    def generate_briefing(self) -> dict:
        sections = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {}
            futures[pool.submit(self._get_weather)] = "weather"
            futures[pool.submit(self._get_news)] = "news"
            futures[pool.submit(self._get_calendar)] = "appointments"
            futures[pool.submit(self._get_tasks)] = "tasks"

            for future in as_completed(futures, timeout=30):
                key = futures[future]
                try:
                    result = future.result()
                    if result:
                        sections[key] = result
                except Exception as e:
                    logger.warning("[Briefing] %s failed: %s", key, e)

        return sections

    def format_briefing(self, sections: dict) -> str:
        now = datetime.now()
        hour = now.hour
        greet = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
        date_str = now.strftime("%A, %d. %B %Y")

        lines = [f"**{greet}! Here is your briefing for {date_str}:**\n"]

        if "weather" in sections:
            lines.append(f"**Weather:** {sections['weather']}\n")

        if "appointments" in sections:
            lines.append(f"**Appointments today:**\n{sections['appointments']}\n")

        if "tasks" in sections:
            lines.append(f"**Open tasks:**\n{sections['tasks']}\n")

        if "news" in sections:
            lines.append(f"**News:**\n{sections['news']}\n")

        if len(lines) == 1:
            lines.append("No data available. Configure the calendar and tasks in the settings.")

        return "\n".join(lines)

    def _get_weather(self) -> Optional[str]:
        import requests
        location = self.config.get("location", "")
        if not location:
            return None
        try:
            r = requests.get(
                f"https://wttr.in/{location}?format=3&lang=de",
                timeout=10,
                headers={"User-Agent": "JARVIS/2.2"}
            )
            if r.status_code == 200:
                return r.text.strip()
        except Exception as e:
            logger.debug("[Briefing] Wetter fehlgeschlagen: %s", e)
        return None

    def _get_news(self) -> Optional[str]:
        try:
            import feedparser
        except ImportError:
            logger.debug("[Briefing] feedparser nicht installiert")
            return None

        feeds = [
            "https://www.tagesschau.de/xml/rss2/",
            "https://rss.orf.at/news.xml",
        ]
        articles = []
        for url in feeds:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    title = entry.get("title", "")
                    if title and title not in [a["title"] for a in articles]:
                        articles.append({
                            "title": title,
                            "link": entry.get("link", ""),
                        })
            except Exception as e:
                logger.debug("[Briefing] Feed %s fehlgeschlagen: %s", url, e)

        if not articles:
            return None

        lines = [f"- {a['title']}" for a in articles[:5]]
        return "\n".join(lines)

    def _get_calendar(self) -> Optional[str]:
        if not self.calendar or not self.calendar.is_configured:
            return None
        try:
            events = self.calendar.get_events(days=1)
            if events:
                return self.calendar.format_events_text(events)
        except Exception as e:
            logger.debug("[Briefing] Kalender fehlgeschlagen: %s", e)
        return None

    def _get_tasks(self) -> Optional[str]:
        if not self.tasks or not self.tasks.is_configured:
            return None
        try:
            task_list = self.tasks.get_all_tasks()
            if task_list:
                return self.tasks.format_tasks_text(task_list[:5])
        except Exception as e:
            logger.debug("[Briefing] Tasks fehlgeschlagen: %s", e)
        return None

    def start_scheduler(self, callback):
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._stop_event.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, args=(callback,), daemon=True)
        self._scheduler_thread.start()

    def stop_scheduler(self):
        self._stop_event.set()

    def _scheduler_loop(self, callback):
        briefing_time = self.config.get("briefing_time", "08:00")
        last_briefing_date = None

        while not self._stop_event.is_set():
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            today = now.date()

            if current_time == briefing_time and last_briefing_date != today:
                last_briefing_date = today
                try:
                    sections = self.generate_briefing()
                    text = self.format_briefing(sections)
                    callback(text)
                except Exception as e:
                    logger.error("[Briefing] Scheduler-Fehler: %s", e)

            self._stop_event.wait(30)
