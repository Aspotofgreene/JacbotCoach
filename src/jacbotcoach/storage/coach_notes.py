"""
Append-only store for coaching session summaries.

Each session summary is saved with a date heading to memory/coach-notes.md.
Recent notes are injected back into new coaching sessions for continuity.
"""

import re
from pathlib import Path


class CoachNotesStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, date_str: str, summary: str) -> None:
        """Append a new session summary under a dated heading."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = f"\n## {date_str}\n{summary.strip()}\n"
        with self._path.open("a") as f:
            f.write(entry)

    def read_recent(self, n: int = 3) -> str:
        """Return the last n session summaries as a single string."""
        if not self._path.exists():
            return ""
        content = self._path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        # Split on section headings (## YYYY-MM-DD ...)
        sections = re.split(r"\n(?=## )", content)
        recent = sections[-n:] if len(sections) > n else sections
        return "\n\n".join(s.strip() for s in recent)

    def is_empty(self) -> bool:
        return not self._path.exists() or not self._path.read_text(encoding="utf-8").strip()
