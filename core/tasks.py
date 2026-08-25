"""
My Jarvis task management
- Todoist integration
- Notion integration
- one interface for both providers
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, config: dict):
        self.config = config
        self._todoist = None
        self._notion = None
        self._init_providers()

    def _init_providers(self):
        provider = self.config.get("task_provider", "todoist")
        todoist_key = self.config.get("todoist_api_key", "")
        notion_key = self.config.get("notion_api_key", "")
        notion_db = self.config.get("notion_database_id", "")

        if todoist_key and provider in ("todoist", "both"):
            try:
                from todoist_api_python.api import TodoistAPI
                self._todoist = TodoistAPI(todoist_key)
                logger.info("[Tasks] Todoist verbunden")
            except ImportError:
                logger.info("[Tasks] todoist-api-python is not installed")
            except Exception as e:
                logger.error("[Tasks] Todoist init failed: %s", e)

        if notion_key and notion_db and provider in ("notion", "both"):
            try:
                from notion_client import Client
                self._notion = Client(auth=notion_key)
                self._notion_db = notion_db
                logger.info("[Tasks] Notion verbunden")
            except ImportError:
                logger.info("[Tasks] notion-client is not installed")
            except Exception as e:
                logger.error("[Tasks] Notion init failed: %s", e)

    @property
    def is_configured(self) -> bool:
        return self._todoist is not None or self._notion is not None

    def get_provider_name(self) -> str:
        parts = []
        if self._todoist:
            parts.append("Todoist")
        if self._notion:
            parts.append("Notion")
        return " + ".join(parts) if parts else "Not configured"

    # ── Todoist ──────────────────────────────────────────────────────────
    def get_todoist_tasks(self, filter_str: str = "today") -> list:
        if not self._todoist:
            return []
        try:
            tasks = self._todoist.get_tasks(filter=filter_str)
            return [{"id": t.id, "content": t.content,
                     "due": t.due.string if t.due else "",
                     "priority": t.priority, "source": "todoist"}
                    for t in tasks]
        except Exception as e:
            logger.error("[Tasks] Todoist get_tasks failed: %s", e)
            return []

    def add_todoist_task(self, content: str, due_string: str = "") -> Optional[dict]:
        if not self._todoist:
            return None
        try:
            kwargs = {"content": content}
            if due_string:
                kwargs["due_string"] = due_string
            task = self._todoist.add_task(**kwargs)
            logger.info("[Tasks] Todoist task created: %s", content)
            return {"id": task.id, "content": task.content, "source": "todoist"}
        except Exception as e:
            logger.error("[Tasks] Todoist add_task failed: %s", e)
            return None

    def complete_todoist_task(self, task_id: str) -> bool:
        if not self._todoist:
            return False
        try:
            self._todoist.close_task(task_id)
            logger.info("[Tasks] Todoist Task erledigt: %s", task_id)
            return True
        except Exception as e:
            logger.error("[Tasks] Todoist completing it failed: %s", e)
            return False

    # ── Notion ───────────────────────────────────────────────────────────
    def get_notion_tasks(self) -> list:
        if not self._notion:
            return []
        try:
            result = self._notion.databases.query(
                database_id=self._notion_db,
                filter={
                    "property": "Status",
                    "status": {"does_not_equal": "Done"}
                }
            )
            tasks = []
            for page in result.get("results", []):
                title_prop = page.get("properties", {}).get("Name", {})
                title_parts = title_prop.get("title", [])
                title = title_parts[0]["plain_text"] if title_parts else "(No title)"
                due_prop = page.get("properties", {}).get("Due", {})
                due = due_prop.get("date", {}).get("start", "") if due_prop.get("date") else ""
                tasks.append({
                    "id": page["id"], "content": title,
                    "due": due, "source": "notion"
                })
            return tasks
        except Exception as e:
            logger.error("[Tasks] Notion get_tasks failed: %s", e)
            return []

    def add_notion_task(self, title: str, due_date: str = "") -> Optional[dict]:
        if not self._notion:
            return None
        try:
            properties = {
                "Name": {"title": [{"text": {"content": title}}]}
            }
            if due_date:
                properties["Due"] = {"date": {"start": due_date}}
            page = self._notion.pages.create(
                parent={"database_id": self._notion_db},
                properties=properties
            )
            logger.info("[Tasks] Notion task created: %s", title)
            return {"id": page["id"], "content": title, "source": "notion"}
        except Exception as e:
            logger.error("[Tasks] Notion add_task failed: %s", e)
            return None

    # ── Unified Interface ────────────────────────────────────────────────
    def get_all_tasks(self) -> list:
        tasks = []
        if self._todoist:
            tasks.extend(self.get_todoist_tasks())
        if self._notion:
            tasks.extend(self.get_notion_tasks())
        return tasks

    def add_task(self, content: str, due: str = "") -> Optional[dict]:
        provider = self.config.get("task_provider", "todoist")
        if provider == "notion" and self._notion:
            return self.add_notion_task(content, due)
        if self._todoist:
            return self.add_todoist_task(content, due)
        if self._notion:
            return self.add_notion_task(content, due)
        return None

    def format_tasks_text(self, tasks: list) -> str:
        if not tasks:
            return "No open tasks."
        lines = []
        for t in tasks:
            due = f" (due: {t['due']})" if t.get("due") else ""
            src = f" [{t['source']}]" if t.get("source") else ""
            lines.append(f"- {t['content']}{due}{src}")
        return "\n".join(lines)
