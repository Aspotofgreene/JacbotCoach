from datetime import date
from pathlib import Path


class IdeaStore:
    """
    Append-only store for quick ideas in memory/ideas.md.
    Each idea is a markdown list item with a date prefix.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, text: str) -> int:
        """Append an idea and return the total count."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        line = f"- [{today}] {text.strip()}\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
        return self.count()

    def read_all(self) -> list[str]:
        """Return all idea lines (raw markdown list items)."""
        if not self.path.exists():
            return []
        lines = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("- "):
                lines.append(line)
        return lines

    def count(self) -> int:
        return len(self.read_all())

    def remove(self, text: str) -> bool:
        """
        Remove the first idea whose text matches (case-insensitive substring).
        Returns True if found and removed.
        """
        if not self.path.exists():
            return False
        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        needle = text.strip().lower()
        for i, line in enumerate(lines):
            if line.strip().startswith("- ") and needle in line.lower():
                del lines[i]
                self.path.write_text("".join(lines), encoding="utf-8")
                return True
        return False

    def extract_text(self, raw_line: str) -> str:
        """Strip the date prefix from a raw list line to get the plain idea text."""
        # Format: - [YYYY-MM-DD] <text>
        import re
        m = re.match(r"^- \[\d{4}-\d{2}-\d{2}\] (.+)$", raw_line)
        return m.group(1) if m else raw_line.lstrip("- ").strip()
