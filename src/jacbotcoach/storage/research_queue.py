import asyncio
import json
from datetime import date
from pathlib import Path


class ResearchQueue:
    """Manages the research topic queue stored in memory/research-queue.json."""

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

    async def enqueue(self, topic: str) -> tuple[int, bool]:
        """Add a topic to the queue.

        Returns (queue_length, already_done) where already_done=True means
        this topic was already completed. Silently skips if already pending.
        """
        async with self._lock:
            items = self._load()
            already_pending = any(
                i["topic"].lower() == topic.lower() and i["status"] == "pending"
                for i in items
            )
            if already_pending:
                return len(items), False
            already_done = any(
                i["topic"].lower() == topic.lower() and i["status"] == "done"
                for i in items
            )
            if already_done:
                return len(items), True
            items.append({
                "topic": topic,
                "queued_at": date.today().isoformat(),
                "status": "pending",
            })
            self._save(items)
            return len(items), False

    def get_pending(self) -> list[dict]:
        return [i for i in self._load() if i["status"] == "pending"]

    def get_all(self) -> list[dict]:
        return self._load()

    async def mark_done(self, topic: str, output_path: str) -> None:
        """Mark a topic as fully completed with the output file path."""
        async with self._lock:
            items = self._load()
            for item in items:
                if item["topic"] == topic and item["status"] == "pending":
                    item["status"] = "done"
                    item["output_path"] = output_path
                    item["done_at"] = date.today().isoformat()
                    break
            self._save(items)

    async def mark_failed(self, topic: str, error: str) -> None:
        async with self._lock:
            items = self._load()
            for item in items:
                if item["topic"] == topic and item["status"] == "pending":
                    item["status"] = "failed"
                    item["error"] = error[:200]
                    break
            self._save(items)

    async def reset_to_pending(self, topic: str) -> bool:
        """Reset a failed or done item back to pending so it will be re-run.
        Returns True if an item was found and reset."""
        async with self._lock:
            items = self._load()
            for item in items:
                if item["topic"].lower() == topic.lower() and item["status"] in ("failed", "done"):
                    item["status"] = "pending"
                    item.pop("error", None)
                    item.pop("output_path", None)
                    item.pop("done_at", None)
                    self._save(items)
                    return True
            return False
