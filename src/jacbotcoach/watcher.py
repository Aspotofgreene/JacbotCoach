"""
Task log file watcher.

Polls memory/tasks-log.md every 30 seconds for new lines written by
OpenClaw agents. When a DONE or UPDATE line appears, sends a Telegram
notification so you know a task completed without checking manually.
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30  # seconds


async def watch_tasks_log(path: Path, bot, chat_id: int) -> None:
    """
    Background coroutine — runs alongside the Telegram bot.
    Tracks the byte position of tasks-log.md and sends a message
    whenever OpenClaw appends a DONE or UPDATE line.
    """
    last_pos = _current_size(path)
    logger.info("Task log watcher started (polling every %ds): %s", _POLL_INTERVAL, path)

    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            current_size = _current_size(path)
            if current_size <= last_pos:
                continue

            new_lines = _read_new_lines(path, last_pos)
            last_pos = current_size

            for line in new_lines:
                line = line.strip()
                if not line.startswith("-"):
                    continue
                if "[DONE]" in line:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"✅ Task completed:\n{_format_log_line(line)}",
                    )
                elif "[UPDATE]" in line:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"📝 Progress update:\n{_format_log_line(line)}",
                    )
                # SCHEDULED and SPAWN_FAILED lines are skipped — those come
                # from the bot itself and would be noisy duplicates.

        except Exception:
            logger.exception("Task log watcher error (will retry)")


def _current_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _read_new_lines(path: Path, from_byte: int) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(from_byte)
            return f.read().decode(errors="replace").splitlines()
    except Exception:
        return []


def _format_log_line(line: str) -> str:
    """Strip the date/status prefix for a cleaner notification."""
    # Format: - [2026-04-02] [DONE] ✅ Task description
    import re
    cleaned = re.sub(r"^\s*-\s*\[\d{4}-\d{2}-\d{2}\]\s*\[\w+\]\s*[✅📝🔄]?\s*", "", line)
    return cleaned.strip() or line.strip()
