"""
Streak tracker for daily habits.

Stores JSON at memory/streaks.json:
{
  "Daily Exercise": {"current": 5, "best": 12, "last_date": "2026-04-03"},
  ...
}
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_MOTIVATIONAL = [
    (1,  "Every journey starts with a single step. Day 1 — let's go!"),
    (3,  "3 days in. The habit is taking root."),
    (7,  "One full week! That's real commitment."),
    (14, "Two weeks strong. This is becoming part of who you are."),
    (21, "21 days — science says habits are forming. Keep going."),
    (30, "30-day streak! You've built something real."),
    (60, "60 days. Most people quit long before this. You didn't."),
    (90, "90 days. This isn't a streak anymore — it's a lifestyle."),
]


class StreakStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def record(self, habit: str) -> dict:
        """
        Mark a habit as done today. Returns updated streak info:
        {"current": int, "best": int, "new_best": bool, "motivation": str}
        """
        data = self._load()
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        entry = data.get(habit, {"current": 0, "best": 0, "last_date": None})

        if entry["last_date"] == today:
            # Already recorded today — no change
            return {"current": entry["current"], "best": entry["best"],
                    "new_best": False, "motivation": ""}

        if entry["last_date"] == yesterday:
            entry["current"] += 1
        else:
            entry["current"] = 1  # streak broken, restart

        new_best = entry["current"] > entry["best"]
        if new_best:
            entry["best"] = entry["current"]
        entry["last_date"] = today

        data[habit] = entry
        self._save(data)

        return {
            "current": entry["current"],
            "best": entry["best"],
            "new_best": new_best,
            "motivation": _motivation_for(entry["current"]),
        }

    def get_all(self) -> dict:
        """Return all streaks. Marks streaks broken if last_date is not yesterday/today."""
        data = self._load()
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        result = {}
        for habit, entry in data.items():
            active = entry.get("last_date") in (today, yesterday)
            result[habit] = {
                "current": entry["current"] if active else 0,
                "best": entry["best"],
                "last_date": entry.get("last_date"),
                "active": active,
            }
        return result

    def get(self, habit: str) -> dict | None:
        return self._load().get(habit)


def _motivation_for(days: int) -> str:
    msg = ""
    for threshold, text in _MOTIVATIONAL:
        if days >= threshold:
            msg = text
    return msg
