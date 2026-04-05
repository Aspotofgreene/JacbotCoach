import asyncio
import json
from datetime import date
from pathlib import Path


class ProjectQueue:
    """Manages the overnight project build queue stored in memory/project-queue.json."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = asyncio.Lock()

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, items: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(items, indent=2))

    async def enqueue(self, idea: str) -> int:
        """Add a project idea to the queue. Returns queue length after insertion.
        Silently skips if the idea is already pending (case-insensitive)."""
        async with self._lock:
            items = self._load()
            already_pending = any(
                i["idea"].lower() == idea.lower() and i["status"] == "pending"
                for i in items
            )
            if not already_pending:
                items.append({
                    "idea": idea,
                    "queued_at": date.today().isoformat(),
                    "status": "pending",
                })
                self._save(items)
            return len(items)

    def get_pending(self) -> list[dict]:
        return [i for i in self._load() if i["status"] == "pending"]

    def get_all(self) -> list[dict]:
        return self._load()

    async def mark_spawned(self, idea: str, session_id: str, output_path: str) -> None:
        async with self._lock:
            items = self._load()
            for item in items:
                if item["idea"] == idea and item["status"] == "pending":
                    item["status"] = "spawned"
                    item["session_id"] = session_id
                    item["output_path"] = output_path
                    item["spawned_at"] = date.today().isoformat()
                    break
            self._save(items)

    async def mark_failed(self, idea: str, error: str) -> None:
        async with self._lock:
            items = self._load()
            for item in items:
                if item["idea"] == idea and item["status"] == "pending":
                    item["status"] = "failed"
                    item["error"] = error[:200]
                    break
            self._save(items)
