"""
Inline keyboard callback handlers.

Callback data format: "<action>:<index>"
  goal:done:2   — mark goal at index 2 done
  goal:pause:2  — set goal at index 2 to Paused
  goal:resume:2 — set goal at index 2 to Active
  goal:delete:2 — delete goal at index 2
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from jacbotcoach.config import get_settings
from jacbotcoach.storage.autonomous import AutonomousStore

logger = logging.getLogger(__name__)


def _parse_goals(content: str) -> list[dict]:
    """Extract all goal rows in order: {title, status, difficulty}"""
    goals = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("|") and "|" in line[1:]:
            if all(c in "-| " for c in line):
                continue
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) < 3:
                continue
            if parts[0] in ("Status", "Difficulty", "Frequency", "Goal", "Habit"):
                continue
            goals.append({"status": parts[0], "difficulty": parts[1], "title": parts[2]})
    return goals


async def goal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data  # e.g. "goal:done:2"
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "goal":
        return

    action = parts[1]
    try:
        idx = int(parts[2])
    except ValueError:
        return

    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    goals = _parse_goals(store.read())

    if idx < 0 or idx >= len(goals):
        await query.edit_message_text("Goal not found — the list may have changed.")
        return

    goal = goals[idx]
    title = goal["title"]

    if action == "done":
        ok = store.mark_goal_done(title)
        msg = f"✅ Marked complete: {title}" if ok else f"Could not find goal: {title}"

    elif action == "pause":
        ok = store.update_goal_status(title, "Paused")
        msg = f"⏸ Paused: {title}" if ok else f"Could not find goal: {title}"

    elif action == "resume":
        ok = store.update_goal_status(title, "Active")
        msg = f"▶️ Resumed: {title}" if ok else f"Could not find goal: {title}"

    elif action == "delete":
        ok = store.delete_goal(title)
        msg = f"🗑️ Deleted: {title}" if ok else f"Could not find goal: {title}"

    else:
        msg = f"Unknown action: {action}"

    await query.edit_message_text(msg)
    logger.info("Goal callback: action=%s title=%s ok=%s", action, title, ok if 'ok' in dir() else '?')
