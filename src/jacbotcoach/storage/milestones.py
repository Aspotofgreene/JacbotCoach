"""
Per-goal milestone tracking stored in memory/milestones.md.

Format:
## Goal Title
- [ ] Milestone description
- [x] Completed milestone

Each goal gets a ## section. Milestones are markdown checkboxes.
"""

import re
from pathlib import Path


class MilestoneStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read_raw(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    def _write(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content, encoding="utf-8")

    def _parse(self) -> dict[str, list[dict]]:
        """Return {goal_title: [{"text": str, "done": bool}]}"""
        raw = self._read_raw()
        result: dict[str, list[dict]] = {}
        current_goal = None
        for line in raw.splitlines():
            h = re.match(r"^## (.+)", line)
            if h:
                current_goal = h.group(1).strip()
                result[current_goal] = []
            elif current_goal is not None:
                m = re.match(r"^- \[([ x])\] (.+)", line)
                if m:
                    result[current_goal].append({
                        "text": m.group(2).strip(),
                        "done": m.group(1) == "x",
                    })
        return result

    def _serialize(self, data: dict[str, list[dict]]) -> str:
        lines = []
        for goal, milestones in data.items():
            lines.append(f"## {goal}")
            for m in milestones:
                check = "x" if m["done"] else " "
                lines.append(f"- [{check}] {m['text']}")
            lines.append("")
        return "\n".join(lines)

    def add(self, goal: str, milestone: str) -> bool:
        """Add a milestone to a goal. Returns True if goal was found."""
        data = self._parse()
        # Find goal by partial case-insensitive match
        matched = _find_key(data, goal)
        if matched is None:
            # Create new goal entry
            data[goal] = []
            matched = goal
        data[matched].append({"text": milestone, "done": False})
        self._write(self._serialize(data))
        return True

    def complete(self, goal: str, milestone_text: str) -> bool:
        """Mark a milestone as done. Returns True if found."""
        data = self._parse()
        matched = _find_key(data, goal)
        if matched is None:
            return False
        for m in data[matched]:
            if milestone_text.lower() in m["text"].lower():
                m["done"] = True
                self._write(self._serialize(data))
                return True
        return False

    def get(self, goal: str) -> list[dict] | None:
        """Return milestones for a goal, or None if not found."""
        data = self._parse()
        matched = _find_key(data, goal)
        return data[matched] if matched else None

    def summary(self, goal: str) -> str:
        """Return a formatted milestone summary string."""
        milestones = self.get(goal)
        if milestones is None:
            return "No milestones set for that goal."
        if not milestones:
            return "No milestones added yet."
        done = sum(1 for m in milestones if m["done"])
        lines = [f"Milestones ({done}/{len(milestones)} done):"]
        for m in milestones:
            icon = "✅" if m["done"] else "⬜"
            lines.append(f"  {icon} {m['text']}")
        return "\n".join(lines)

    def progress_pct(self, goal: str) -> int | None:
        """Return completion percentage (0-100), or None if no milestones."""
        milestones = self.get(goal)
        if not milestones:
            return None
        done = sum(1 for m in milestones if m["done"])
        return round(done / len(milestones) * 100)

    def all_goals(self) -> list[str]:
        return list(self._parse().keys())


def _find_key(data: dict, query: str) -> str | None:
    query_lower = query.lower()
    for key in data:
        if query_lower in key.lower():
            return key
    return None
