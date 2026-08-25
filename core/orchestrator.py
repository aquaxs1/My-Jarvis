"""
My Jarvis multi-agent orchestrator
- splits complex tasks into sub-tasks
- coordinates sub-agents in parallel
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class SubAgent:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def run(self, task: str, context: dict = None) -> dict:
        raise NotImplementedError


class ResearchAgent(SubAgent):
    def __init__(self, research_mgr):
        super().__init__("ResearchAgent", "Web-Recherche und Zusammenfassung")
        self.research = research_mgr

    def run(self, task: str, context: dict = None) -> dict:
        try:
            articles = self.research.fetch_articles()
            text = self.research.format_research_text(articles[:5])
            return {"status": "ok", "result": text, "agent": self.name}
        except Exception as e:
            return {"status": "error", "result": str(e), "agent": self.name}


class CalendarAgent(SubAgent):
    def __init__(self, calendar_mgr):
        super().__init__("CalendarAgent", "Termine und Deadlines verwalten")
        self.calendar = calendar_mgr

    def run(self, task: str, context: dict = None) -> dict:
        try:
            if not self.calendar or not self.calendar.is_configured:
                return {"status": "skip", "result": "Kalender is not configured", "agent": self.name}
            events = self.calendar.get_events(days=7)
            text = self.calendar.format_events_text(events)
            return {"status": "ok", "result": text, "agent": self.name}
        except Exception as e:
            return {"status": "error", "result": str(e), "agent": self.name}


class FileAgent(SubAgent):
    def __init__(self, doc_reader):
        super().__init__("FileAgent", "Reads and organises files")
        self.reader = doc_reader

    def run(self, task: str, context: dict = None) -> dict:
        filepath = context.get("filepath") if context else None
        if not filepath:
            return {"status": "skip", "result": "Kein Dateipfad angegeben", "agent": self.name}
        try:
            text = self.reader.read(filepath)
            if text:
                return {"status": "ok", "result": text[:3000], "agent": self.name}
            return {"status": "error", "result": "The file could not be read", "agent": self.name}
        except Exception as e:
            return {"status": "error", "result": str(e), "agent": self.name}


class Orchestrator:
    def __init__(self, config: dict, gui_callback: Callable = None):
        self.config = config
        self.gui_cb = gui_callback
        self._agents: dict[str, SubAgent] = {}

    def register_agent(self, agent: SubAgent):
        self._agents[agent.name] = agent

    def _emit(self, event: str, data: dict):
        if self.gui_cb:
            try:
                self.gui_cb(event, data)
            except Exception:
                pass

    def create_plan(self, task: str) -> list:
        plan = []

        task_lower = task.lower()

        if any(kw in task_lower for kw in ["recherche", "research", "artikel", "news", "bericht"]):
            plan.append({"agent": "ResearchAgent", "task": task, "context": {}})

        if any(kw in task_lower for kw in ["termin", "kalender", "meeting", "woche"]):
            plan.append({"agent": "CalendarAgent", "task": task, "context": {}})

        if any(kw in task_lower for kw in ["file", "document", "pdf", "read"]):
            plan.append({"agent": "FileAgent", "task": task, "context": {}})

        if not plan:
            plan.append({"agent": "ResearchAgent", "task": task, "context": {}})

        return plan

    def execute(self, task: str) -> dict:
        plan = self.create_plan(task)

        self._emit("orchestrator_plan", {
            "task": task,
            "agents": [p["agent"] for p in plan],
        })

        results = {}
        with ThreadPoolExecutor(max_workers=len(plan)) as pool:
            futures = {}
            for step in plan:
                agent = self._agents.get(step["agent"])
                if not agent:
                    continue
                self._emit("orchestrator_agent_start", {"agent": step["agent"]})
                future = pool.submit(agent.run, step["task"], step.get("context", {}))
                futures[future] = step["agent"]

            for future in as_completed(futures, timeout=60):
                agent_name = futures[future]
                try:
                    result = future.result()
                    results[agent_name] = result
                    self._emit("orchestrator_agent_done", {
                        "agent": agent_name,
                        "status": result.get("status", "unknown"),
                    })
                except Exception as e:
                    results[agent_name] = {"status": "error", "result": str(e)}
                    self._emit("orchestrator_agent_done", {
                        "agent": agent_name, "status": "error"
                    })

        combined = self._combine_results(task, results)
        self._emit("orchestrator_complete", {"task": task})
        return combined

    def _combine_results(self, task: str, results: dict) -> dict:
        parts = []
        for agent_name, result in results.items():
            if result.get("status") == "ok" and result.get("result"):
                parts.append(f"**[{agent_name}]**\n{result['result']}")
            elif result.get("status") == "error":
                parts.append(f"**[{agent_name}]** Error: {result.get('result', 'Unknown')}")

        return {
            "task": task,
            "result": "\n\n---\n\n".join(parts) if parts else "No results.",
            "agents_used": list(results.keys()),
        }
