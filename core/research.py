"""
My Jarvis research assistant
- fetches RSS feeds, arXiv and Hacker News
- a daily research briefing
"""
import logging
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class ResearchAssistant:
    def __init__(self, config: dict):
        self.config = config
        self._last_articles = []
        self._stop_event = threading.Event()
        self._thread = None

    @property
    def topics(self) -> list:
        return self.config.get("research_topics", [])

    def fetch_articles(self) -> list:
        articles = []
        articles.extend(self._fetch_rss())
        articles.extend(self._fetch_hackernews())
        articles.extend(self._fetch_arxiv())

        articles.sort(key=lambda a: a.get("date", ""), reverse=True)
        self._last_articles = articles
        return articles

    def _fetch_rss(self) -> list:
        try:
            import feedparser
        except ImportError:
            return []

        feeds = self.config.get("research_feeds", [
            "https://www.tagesschau.de/xml/rss2/",
        ])
        articles = []

        for url in feeds:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    articles.append({
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "summary": entry.get("summary", "")[:200],
                        "date": entry.get("published", ""),
                        "source": "RSS",
                    })
            except Exception as e:
                logger.debug("[Research] RSS %s failed: %s", url, e)

        return articles

    def _fetch_hackernews(self) -> list:
        import requests
        articles = []
        try:
            r = requests.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                timeout=10
            )
            r.raise_for_status()
            ids = r.json()[:5]

            for story_id in ids:
                try:
                    sr = requests.get(
                        f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                        timeout=5
                    )
                    sr.raise_for_status()
                    story = sr.json()
                    if story and story.get("title"):
                        articles.append({
                            "title": story["title"],
                            "link": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                            "summary": f"Score: {story.get('score', 0)} | {story.get('descendants', 0)} Kommentare",
                            "date": "",
                            "source": "Hacker News",
                        })
                except Exception:
                    continue
        except Exception as e:
            logger.debug("[Research] Hacker News failed: %s", e)

        return articles

    def _fetch_arxiv(self) -> list:
        if not self.topics:
            return []

        import requests
        articles = []
        query = "+OR+".join(f'all:"{t}"' for t in self.topics[:3])

        try:
            r = requests.get(
                f"http://export.arxiv.org/api/query?search_query={query}&start=0&max_results=5&sortBy=submittedDate&sortOrder=descending",
                timeout=15
            )
            r.raise_for_status()

            import xml.etree.ElementTree as ET
            root = ET.fromstring(r.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            for entry in root.findall("atom:entry", ns):
                title = entry.find("atom:title", ns)
                summary = entry.find("atom:summary", ns)
                link = entry.find("atom:id", ns)
                published = entry.find("atom:published", ns)

                if title is not None:
                    articles.append({
                        "title": " ".join(title.text.split()),
                        "link": link.text if link is not None else "",
                        "summary": " ".join(summary.text.split())[:200] if summary is not None else "",
                        "date": published.text if published is not None else "",
                        "source": "arXiv",
                    })
        except Exception as e:
            logger.debug("[Research] arXiv failed: %s", e)

        return articles

    def format_research_text(self, articles: list = None) -> str:
        if articles is None:
            articles = self._last_articles
        if not articles:
            return "No articles found."

        lines = []
        for i, a in enumerate(articles[:10], 1):
            src = f"[{a['source']}]" if a.get("source") else ""
            lines.append(f"**{i}.** {a['title']} {src}\n   {a['summary']}")

        return "\n\n".join(lines)

    def get_article_detail(self, index: int) -> Optional[dict]:
        if 0 < index <= len(self._last_articles):
            return self._last_articles[index - 1]
        return None

    def start_scheduler(self, callback):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop, args=(callback,), daemon=True)
        self._thread.start()

    def stop_scheduler(self):
        self._stop_event.set()

    def _scheduler_loop(self, callback):
        research_time = self.config.get("research_time", "09:00")
        last_date = None

        while not self._stop_event.is_set():
            now = datetime.now()
            if now.strftime("%H:%M") == research_time and last_date != now.date():
                last_date = now.date()
                try:
                    articles = self.fetch_articles()
                    text = self.format_research_text(articles)
                    callback(f"**Research-Briefing:**\n\n{text}")
                except Exception as e:
                    logger.error("[Research] scheduler error: %s", e)

            self._stop_event.wait(30)
