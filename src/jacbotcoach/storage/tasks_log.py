import asyncio
from datetime import date
from pathlib import Path


class TasksLog:
    """
    Append-only task log (memory/tasks-log.md).

    Rules:
    - Never edit existing lines — only append new ones.
    - asyncio.Lock prevents concurrent writes from the scheduler
      and any manual bot triggers at the same instant.
    - Sub-agents follow the same rule: append ✅ lines at the bottom only.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def append(self, task: str, status: str = "SCHEDULED") -> None:
        async with self._lock:
            today = date.today().isoformat()
            icon = "✅" if status.upper() == "DONE" else "🔄"
            entry = f"- [{today}] [{status.upper()}] {icon} {task}\n"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(entry)

    def read_today(self) -> list[str]:
        today = date.today().isoformat()
        if not self.path.exists():
            return []
        return [
            line.strip()
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if today in line and line.strip().startswith("-")
        ]

    def read_all(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")
