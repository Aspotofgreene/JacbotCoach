import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


class CheckinStore:
    """
    Stores today's 3 daily deliverables and tracks completion + notes.

    File format (memory/checkins.json):
    {
        "date": "2026-04-06",
        "deliverables": [
            {"id": 1, "text": "Finish Chapter 1 by 11 AM", "done": false, "notes": []},
            {"id": 2, "text": "Email clients by 2 PM",     "done": false, "notes": []},
            {"id": 3, "text": "Code review by 5 PM",       "done": false, "notes": []}
        ]
    }

    A new day resets deliverables — yesterday's data is replaced when set_deliverables()
    is called with a new date.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("checkins.json unreadable — returning empty")
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def has_today(self) -> bool:
        """Return True if deliverables have been set for today."""
        data = self._load()
        return data.get("date") == date.today().isoformat() and bool(data.get("deliverables"))

    def get_today(self) -> list[dict]:
        """Return today's deliverables list, or [] if none set."""
        data = self._load()
        if data.get("date") != date.today().isoformat():
            return []
        return data.get("deliverables", [])

    def set_deliverables(self, texts: list[str]) -> None:
        """
        Set (or replace) today's deliverables. Pass a list of 1-3 strings.
        Previous day's data is silently replaced.
        """
        deliverables = [
            {"id": i + 1, "text": t.strip(), "done": False, "notes": []}
            for i, t in enumerate(texts[:3])
        ]
        self._save({"date": date.today().isoformat(), "deliverables": deliverables})

    def mark_done(self, n: int) -> bool:
        """
        Mark deliverable number n (1-indexed) as done.
        Returns True if found and updated, False otherwise.
        """
        data = self._load()
        if data.get("date") != date.today().isoformat():
            return False
        for d in data.get("deliverables", []):
            if d["id"] == n:
                d["done"] = True
                self._save(data)
                return True
        return False

    def add_note(self, n: int, note: str) -> bool:
        """
        Append a progress note or obstacle note to deliverable n (1-indexed).
        Returns True if found, False otherwise.
        """
        data = self._load()
        if data.get("date") != date.today().isoformat():
            return False
        for d in data.get("deliverables", []):
            if d["id"] == n:
                d["notes"].append(note.strip())
                self._save(data)
                return True
        return False

    def summary_text(self) -> str:
        """
        Return a human-readable status block for today's deliverables.
        Used by check-in messages and /deliverables command.
        """
        items = self.get_today()
        if not items:
            return "No deliverables set for today. Send me your top 3 to get started!"
        lines = [f"Today's deliverables ({date.today().strftime('%A, %b %d')}):\n"]
        for d in items:
            icon = "✅" if d["done"] else "⬜"
            lines.append(f"{icon} {d['id']}. {d['text']}")
            for note in d["notes"]:
                lines.append(f"     💬 {note}")
        done_count = sum(1 for d in items if d["done"])
        lines.append(f"\nProgress: {done_count}/{len(items)} complete")
        return "\n".join(lines)
