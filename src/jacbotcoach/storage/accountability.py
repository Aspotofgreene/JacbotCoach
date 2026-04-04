"""
Accountability score tracker.

Stores weekly 1-10 scores across three dimensions in memory/accountability-scores.json:
{
  "2026-03-29": {
    "week_ending": "2026-03-29",
    "consistency": 7,
    "focus_alignment": 6,
    "momentum": 8,
    "overall": 7.0,
    "done_count": 14,
    "scheduled_count": 20,
    "insight": "Good week — solid consistency but focus drifted mid-week."
  },
  ...
}
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class AccountabilityStore:
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

    def record(
        self,
        week_ending: str,
        consistency: int,
        focus_alignment: int,
        momentum: int,
        done_count: int,
        scheduled_count: int,
        insight: str,
    ) -> None:
        """Save a weekly accountability score. week_ending is YYYY-MM-DD (Sunday)."""
        data = self._load()
        overall = round((consistency + focus_alignment + momentum) / 3, 1)
        data[week_ending] = {
            "week_ending": week_ending,
            "consistency": consistency,
            "focus_alignment": focus_alignment,
            "momentum": momentum,
            "overall": overall,
            "done_count": done_count,
            "scheduled_count": scheduled_count,
            "insight": insight,
        }
        self._save(data)

    def get_latest(self) -> dict | None:
        """Return the most recent weekly score entry, or None if empty."""
        data = self._load()
        if not data:
            return None
        latest_key = max(data.keys())
        return data[latest_key]

    def get_history(self, weeks: int = 12) -> list[dict]:
        """Return the last N weekly scores, most recent first."""
        data = self._load()
        sorted_keys = sorted(data.keys(), reverse=True)
        return [data[k] for k in sorted_keys[:weeks]]

    def get_previous_done_count(self, before_date: str) -> int:
        """
        Return done_count from the week prior to before_date.
        Used for momentum calculation.
        """
        data = self._load()
        keys_before = [k for k in sorted(data.keys()) if k < before_date]
        if not keys_before:
            return 0
        return data[max(keys_before)].get("done_count", 0)
