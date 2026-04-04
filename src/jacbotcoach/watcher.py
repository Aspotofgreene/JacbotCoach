"""
Task log file watcher.

Polls memory/tasks-log.md every 30 seconds for new lines written by
OpenClaw agents. When a DONE or UPDATE line appears:
  - Sends a Telegram notification
  - Attempts to match the task to a goal and update goal status
  - Checks if the task relates to a habit and updates its streak
"""

import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30  # seconds


async def watch_tasks_log(path: Path, bot, chat_id: int) -> None:
    """
    Background coroutine — runs alongside the Telegram bot.
    Tracks the byte position of tasks-log.md and processes new DONE/UPDATE lines.
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
                    task_desc = _format_log_line(line)
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"✅ Task completed:\n{task_desc}",
                    )
                    # Background: try to link task to a goal and update status
                    asyncio.create_task(_link_task_to_goal(task_desc, bot, chat_id))
                    # Background: check if it's habit-related and update streak
                    asyncio.create_task(_auto_streak(task_desc, bot, chat_id))

                elif "[UPDATE]" in line:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"📝 Progress update:\n{_format_log_line(line)}",
                    )
                # SCHEDULED and SPAWN_FAILED lines are skipped

        except Exception:
            logger.exception("Task log watcher error (will retry)")


async def _link_task_to_goal(task_desc: str, bot, chat_id: int) -> None:
    """
    Use LLM to match the completed task to a goal, then advance its status
    from Active → In Progress (if it wasn't already further along).
    """
    try:
        from jacbotcoach.config import get_settings
        from jacbotcoach.llm.router import LLMRouter
        from jacbotcoach.storage.autonomous import AutonomousStore

        settings = get_settings()
        store = AutonomousStore(settings.autonomous_md_path)
        if store.is_empty():
            return

        goals_content = store.read()
        matched_goal = await LLMRouter().match_task_to_goal(task_desc, goals_content)

        if matched_goal:
            # Only advance from Active → In Progress; don't downgrade further statuses
            advanced = store.update_goal_status_if(matched_goal, "Active", "In Progress")
            if advanced:
                logger.info("Advanced goal '%s' to In Progress", matched_goal)
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"📈 Goal updated to *In Progress*: {matched_goal}",
                    parse_mode="Markdown",
                )
    except Exception:
        logger.debug("Task-to-goal linking failed (non-critical)", exc_info=True)


async def _auto_streak(task_desc: str, bot, chat_id: int) -> None:
    """
    If the completed task looks like a habit completion, record it in the streak store.
    Sends a streak notification only if a milestone is hit.
    """
    try:
        from jacbotcoach.config import get_settings
        from jacbotcoach.storage.autonomous import AutonomousStore
        from jacbotcoach.storage.streaks import StreakStore

        settings = get_settings()
        store = AutonomousStore(settings.autonomous_md_path)
        if store.is_empty():
            return

        # Extract habit titles from the Habits & Ongoing section
        content = store.read()
        in_habits = False
        habit_titles = []
        for line in content.splitlines():
            if "## Habits" in line:
                in_habits = True
                continue
            if in_habits and line.startswith("##"):
                in_habits = False
            if in_habits and line.startswith("|"):
                parts = [p.strip() for p in line.strip("|").split("|")]
                if len(parts) >= 3 and parts[0] not in ("Status", "Frequency", "Habit", "---"):
                    habit_titles.append(parts[2])  # habit title column

        task_lower = task_desc.lower()
        matched_habit = None
        for habit in habit_titles:
            keywords = [w for w in habit.lower().split() if len(w) > 4]
            if keywords and any(kw in task_lower for kw in keywords):
                matched_habit = habit
                break

        if not matched_habit:
            return

        streak_store = StreakStore(settings.streaks_path)
        result = streak_store.record(matched_habit)

        # Only notify on streak milestones, not every day (avoid noise)
        milestone_days = {1, 3, 7, 14, 21, 30, 60, 90}
        if result["current"] in milestone_days or result["new_best"]:
            lines = [f"🔥 Habit streak: {matched_habit} — Day {result['current']}!"]
            if result["new_best"]:
                lines.append(f"New personal best!")
            if result["motivation"]:
                lines.append(result["motivation"])
            await bot.send_message(chat_id=chat_id, text="\n".join(lines))

    except Exception:
        logger.debug("Auto-streak update failed (non-critical)", exc_info=True)


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
    cleaned = re.sub(r"^\s*-\s*\[\d{4}-\d{2}-\d{2}\]\s*\[\w+\]\s*[✅📝🔄]?\s*", "", line)
    return cleaned.strip() or line.strip()
