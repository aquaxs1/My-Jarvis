"""
My Jarvis finance tracker
- log expenses
- set and watch budgets
- a monthly overview
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

JARVIS_DIR = Path.home() / ".jarvis"
FINANCE_PATH = JARVIS_DIR / "finance.json"

CATEGORIES = [
    "Lebensmittel", "Transport", "Entertainment", "Wohnen",
    "Kleidung", "Gesundheit", "Bildung", "Sonstiges"
]


class FinanceTracker:
    def __init__(self, config: dict):
        self.config = config
        self._data = self._load()

    def _load(self) -> dict:
        JARVIS_DIR.mkdir(parents=True, exist_ok=True)
        if FINANCE_PATH.exists():
            try:
                return json.loads(FINANCE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[Finance] Loading failed: %s", e)
        return {"expenses": [], "budgets": {}}

    def _save(self):
        try:
            FINANCE_PATH.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError as e:
            logger.error("[Finance] Saving failed: %s", e)

    def add_expense(self, amount: float, category: str,
                    description: str = "") -> dict:
        if category not in CATEGORIES:
            best = self._guess_category(description or category)
            category = best if best else "Sonstiges"

        expense = {
            "amount": round(amount, 2),
            "category": category,
            "description": description,
            "date": datetime.now().isoformat(),
        }
        self._data.setdefault("expenses", []).append(expense)
        self._save()
        logger.info("[Finance] Ausgabe: %.2f€ (%s)", amount, category)
        return expense

    def set_budget(self, category: str, amount: float):
        self._data.setdefault("budgets", {})[category] = round(amount, 2)
        self._save()
        logger.info("[Finance] Budget: %s = %.2f€", category, amount)

    def get_expenses(self, days: int = 30) -> list:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        return [e for e in self._data.get("expenses", [])
                if e.get("date", "") >= cutoff]

    def get_summary(self, days: int = 30) -> dict:
        expenses = self.get_expenses(days)
        by_category = {}
        total = 0.0
        for e in expenses:
            cat = e.get("category", "Sonstiges")
            by_category[cat] = by_category.get(cat, 0) + e["amount"]
            total += e["amount"]
        return {"total": round(total, 2), "by_category": by_category, "count": len(expenses)}

    def check_budget_warnings(self) -> list:
        budgets = self._data.get("budgets", {})
        if not budgets:
            return []

        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
        month_expenses = [e for e in self._data.get("expenses", [])
                         if e.get("date", "") >= month_start]

        by_cat = {}
        for e in month_expenses:
            cat = e.get("category", "Sonstiges")
            by_cat[cat] = by_cat.get(cat, 0) + e["amount"]

        warnings = []
        for cat, budget in budgets.items():
            spent = by_cat.get(cat, 0)
            pct = (spent / budget * 100) if budget > 0 else 0
            if pct >= 100:
                warnings.append({
                    "category": cat, "spent": spent, "budget": budget,
                    "percent": round(pct), "level": "critical"
                })
            elif pct >= 80:
                warnings.append({
                    "category": cat, "spent": spent, "budget": budget,
                    "percent": round(pct), "level": "warning"
                })
        return warnings

    def format_summary_text(self, days: int = 30) -> str:
        summary = self.get_summary(days)
        period = "this week" if days <= 7 else f"the last {days} days"

        lines = [f"**Spending ({period}):** {summary['total']:.2f}€ ({summary['count']} entries)\n"]

        for cat, amount in sorted(summary["by_category"].items(),
                                  key=lambda x: x[1], reverse=True):
            budget = self._data.get("budgets", {}).get(cat)
            if budget:
                pct = amount / budget * 100
                bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
                lines.append(f"- **{cat}:** {amount:.2f}€ / {budget:.2f}€ [{bar}] {pct:.0f}%")
            else:
                lines.append(f"- **{cat}:** {amount:.2f}€")

        warnings = self.check_budget_warnings()
        if warnings:
            lines.append("\n**Warnungen:**")
            for w in warnings:
                icon = "🔴" if w["level"] == "critical" else "🟡"
                lines.append(f"{icon} {w['category']}: {w['spent']:.2f}€ / {w['budget']:.2f}€ ({w['percent']}%)")

        return "\n".join(lines)

    def _guess_category(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        keywords = {
            "Lebensmittel": ["essen", "lebensmittel", "supermarkt", "billa", "spar", "hofer",
                             "restaurant", "cafe", "kaffee", "pizza", "burger"],
            "Transport": ["benzin", "tanken", "uber", "taxi", "bahn", "ticket", "bus", "parkplatz"],
            "Entertainment": ["kino", "netflix", "spotify", "spiel", "konzert", "bar", "club"],
            "Wohnen": ["miete", "strom", "gas", "internet", "wasser", "versicherung"],
            "Kleidung": ["kleidung", "schuhe", "jacke", "hose", "shirt", "mode"],
            "Gesundheit": ["arzt", "apotheke", "medikament", "sport", "fitness", "gym"],
            "Bildung": ["buch", "kurs", "udemy", "schule", "uni"],
        }
        for cat, kws in keywords.items():
            if any(kw in text_lower for kw in kws):
                return cat
        return None
