import json
import logging
import re
from telegram import Update
from telegram.ext import ContextTypes

from jacbotcoach.config import get_settings
from jacbotcoach.storage.autonomous import AutonomousStore
from jacbotcoach.storage.focus import FocusStore
from jacbotcoach.storage.tasks_log import TasksLog

logger = logging.getLogger(__name__)


def _is_allowed(update: Update) -> bool:
    settings = get_settings()
    user = update.effective_user
    if user is None:
        logger.warning("Update has no effective_user — ignoring")
        return False
    allowed = user.id == settings.telegram_allowed_user_id
    if not allowed:
        logger.warning(
            "Rejected message from user_id=%s (allowed=%s)",
            user.id,
            settings.telegram_allowed_user_id,
        )
    return allowed


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("start_command received from user_id=%s", update.effective_user and update.effective_user.id)
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        "JacbotCoach online.\n\n"
        "Goals:\n"
        "  /goals           — brain dump your goals (then /save, /confirm)\n"
        "  /goals_list      — view all goals (flat list)\n"
        "  /goals_cat       — view goals by category\n"
        "  /goals_reparse   — re-structure goals through AI (fixes raw text)\n"
        "  /goal_done <title> — mark a goal complete\n"
        "  /goal_status <title> <status> — update goal status\n"
        "  /goal_edit <old> | <new> — rename a goal\n"
        "  /goal_delete <title> — remove a goal\n"
        "  /promote <item>  — move backlog item to active goals\n"
        "  /status          — view the full goals file\n\n"
        "Focus:\n"
        "  /focus           — see current focus goals\n"
        "  /focus <goals>   — set weekly focus goals\n"
        "  /unfocus         — clear focus\n\n"
        "Tasks:\n"
        "  /tasks           — view today's tasks\n"
        "  /trigger         — run task generation now\n"
        "  /done <task>     — mark a task complete\n"
        "  /update <msg>    — log a progress update\n\n"
        "Coaching:\n"
        "  /coach           — start a coaching conversation\n"
        "  /endcoach        — end coaching session\n"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.is_empty():
        await update.message.reply_text("No goals set yet. Use /goals to add them.")
        return
    content = store.read()
    lines = store.line_count()
    await update.message.reply_text(
        f"Goals file ({lines}/50 lines):\n\n{content[:4000]}"
    )


async def goals_reparse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-run LLM structuring on whatever is currently in AUTONOMOUS.md."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.is_empty():
        await update.message.reply_text("No goals found. Use /goals to add them first.")
        return
    raw = store.read()
    await update.message.reply_text("Re-structuring your goals via AI… (this may take 20-30s)")
    try:
        from jacbotcoach.llm.router import LLMRouter
        structured = await LLMRouter().parse_goals(raw)
        store.write(structured)
        lines = store.line_count()
        await update.message.reply_text(
            f"Done. Goals re-structured and saved ({lines} lines).\n\nUse /goals_list or /goals_cat to review."
        )
    except Exception as e:
        logger.error("goals_reparse failed: %s", e)
        await update.message.reply_text(f"Failed to re-parse goals: {e}")


async def goals_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all goals as a flat numbered list regardless of category."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.is_empty():
        await update.message.reply_text("No goals yet. Use /goals to add some.")
        return

    content = store.read()
    # Extract all table rows (lines with | that aren't headers or separators)
    goals = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("|") and "|" in line[1:]:
            if all(c in "-| " for c in line):
                continue  # separator row
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) < 3:
                continue
            # Skip header rows: first cell is "Status", "Difficulty", "Frequency", etc.
            if parts[0] in ("Status", "Difficulty", "Frequency", "Goal", "Habit"):
                continue
            goals.append(parts)

    if not goals:
        await update.message.reply_text(
            "Goals not yet categorized.\nUse /status to see the raw file."
        )
        return

    lines = ["All goals:\n"]
    for i, row in enumerate(goals, 1):
        # Columns: Status | Difficulty/Frequency | Goal title | Notes
        title = row[2] if len(row) > 2 else row[0]
        difficulty = row[1] if len(row) > 1 else ""
        lines.append(f"{i}. [{difficulty}] {title}")

    await update.message.reply_text("\n".join(lines)[:4000])


async def goals_categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show goals organized by category with headers."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.is_empty():
        await update.message.reply_text("No goals yet. Use /goals to add some.")
        return

    content = store.read()
    # Extract each section and format it cleanly
    sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)
    output_parts = []

    for section in sections:
        if not section.strip() or section.startswith("# Goals"):
            continue
        header_match = re.match(r"## (.+)", section)
        if not header_match:
            continue
        header = header_match.group(1).strip()
        rows = []
        for line in section.splitlines():
            line = line.strip()
            if line.startswith("|") and "|" in line[1:]:
                parts = [p.strip() for p in line.strip("|").split("|")]
                if parts and not all(c in "-| " for c in line):
                    if parts[0] not in ("Difficulty", "Frequency", "Goal", "Habit", "---"):
                        label = parts[0]
                        title = parts[1] if len(parts) > 1 else parts[0]
                        rows.append(f"  [{label}] {title}")
            elif line.startswith("- ") and "Open Backlog" in section:
                rows.append(f"  {line}")

        if rows:
            output_parts.append(f"📌 {header}\n" + "\n".join(rows))

    if not output_parts:
        await update.message.reply_text(
            "Goals file doesn't have categories yet.\n"
            "Use /goals to re-enter your goals and they'll be categorized automatically."
        )
        return

    await update.message.reply_text("\n\n".join(output_parts)[:4000])


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    settings = get_settings()
    log = TasksLog(settings.tasks_log_path)
    tasks = log.read_today()
    if not tasks:
        await update.message.reply_text(
            "No tasks logged today yet.\n"
            "The scheduler runs at 8 AM, or use /trigger to run now."
        )
        return
    await update.message.reply_text(
        f"Today's tasks ({len(tasks)}):\n\n" + "\n".join(tasks)
    )


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark a task as complete: /done <task description>"""
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Usage: /done <task description>\n"
            "Example: /done Research Agentic AI concepts"
        )
        return
    settings = get_settings()
    log = TasksLog(settings.tasks_log_path)
    await log.append(text, status="DONE")
    await update.message.reply_text(f"✅ Marked as done:\n{text}")


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log a progress update: /update <message>"""
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Usage: /update <progress note>\n"
            "Example: /update Finished research, starting the write-up"
        )
        return
    settings = get_settings()
    log = TasksLog(settings.tasks_log_path)
    await log.append(text, status="UPDATE")
    await update.message.reply_text(f"📝 Update logged:\n{text}")


async def trigger_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually trigger daily task generation without waiting for 8 AM."""
    if not _is_allowed(update):
        return
    from jacbotcoach.scheduler.jobs import run_daily_job
    await update.message.reply_text("Triggering task generation now...")
    try:
        await run_daily_job(context.application)
    except Exception as e:
        logger.exception("Manual trigger failed")
        await update.message.reply_text(f"Error during task generation: {e}")


async def focus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /focus          — show current focus goals
    /focus <text>   — set weekly focus goals
    """
    if not _is_allowed(update):
        return
    settings = get_settings()
    focus_store = FocusStore(settings.autonomous_md_path.parent / "focus.md")
    text = " ".join(context.args) if context.args else ""

    if text:
        focus_store.set(text)
        await update.message.reply_text(
            f"🎯 Focus set:\n{text}\n\n"
            "Tomorrow's task generation will prioritize these goals.\n"
            "Use /unfocus to clear."
        )
    else:
        current = focus_store.read()
        if current:
            await update.message.reply_text(f"🎯 Current focus:\n{current}")
        else:
            await update.message.reply_text(
                "No focus set. Use /focus <goals> to prioritize.\n"
                "Example: /focus Launch SaaS MVP, Daily exercise habit"
            )


async def unfocus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    settings = get_settings()
    FocusStore(settings.autonomous_md_path.parent / "focus.md").clear()
    await update.message.reply_text("Focus cleared. All goals weighted equally.")


async def goal_done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark a goal as complete and move it to the Completed section."""
    if not _is_allowed(update):
        return
    title = " ".join(context.args) if context.args else ""
    if not title:
        await update.message.reply_text(
            "Usage: /goal_done <goal title>\n"
            "Example: /goal_done Launch SaaS MVP"
        )
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.mark_goal_done(title):
        await update.message.reply_text(
            f"🏆 Goal marked as done and archived:\n{title}"
        )
    else:
        await update.message.reply_text(
            f"Goal not found: '{title}'\n"
            "Use /goals_list to see exact goal titles."
        )


async def goal_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Update a goal's status: /goal_status <title> <status>
    Status: Active | In Progress | Paused | Done
    """
    if not _is_allowed(update):
        return
    args = context.args or []
    valid_statuses = {"Active", "In Progress", "Paused", "Done"}

    # Last arg is status, everything before is the title
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /goal_status <goal title> <status>\n"
            "Status options: Active | In Progress | Paused | Done\n"
            "Example: /goal_status Launch SaaS MVP In Progress"
        )
        return

    # Try matching status from the end
    status = None
    title_parts = list(args)
    for n in (2, 1):
        candidate = " ".join(args[-n:])
        if candidate in valid_statuses:
            status = candidate
            title_parts = args[:-n]
            break

    if not status or not title_parts:
        await update.message.reply_text(
            f"Valid statuses: {', '.join(valid_statuses)}\n"
            "Example: /goal_status Launch SaaS MVP In Progress"
        )
        return

    title = " ".join(title_parts)
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.update_goal_status(title, status):
        await update.message.reply_text(f"Updated '{title}' → {status}")
    else:
        await update.message.reply_text(
            f"Goal not found: '{title}'\nUse /goals_list to see exact titles."
        )


async def goal_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a goal entirely: /goal_delete <title>"""
    if not _is_allowed(update):
        return
    title = " ".join(context.args) if context.args else ""
    if not title:
        await update.message.reply_text(
            "Usage: /goal_delete <goal title>\n"
            "Example: /goal_delete Learn Spanish"
        )
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.delete_goal(title):
        await update.message.reply_text(f"🗑️ Goal deleted:\n{title}")
    else:
        await update.message.reply_text(
            f"Goal not found: '{title}'\n"
            "Use /goals_list to see exact goal titles."
        )


async def goal_edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Rename a goal: /goal_edit <old title> | <new title>
    The pipe character separates old and new title.
    """
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if "|" not in text:
        await update.message.reply_text(
            "Usage: /goal_edit <old title> | <new title>\n"
            "Example: /goal_edit Learn Spanish | Learn Portuguese"
        )
        return
    parts = text.split("|", 1)
    old_title = parts[0].strip()
    new_title = parts[1].strip()
    if not old_title or not new_title:
        await update.message.reply_text("Both old and new titles must be non-empty.")
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.edit_goal_title(old_title, new_title):
        await update.message.reply_text(f"✏️ Goal renamed:\n{old_title} → {new_title}")
    else:
        await update.message.reply_text(
            f"Goal not found: '{old_title}'\n"
            "Use /goals_list to see exact goal titles."
        )


async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Move a backlog item into active goals with LLM classification.
    /promote <item text>
    """
    if not _is_allowed(update):
        return
    item = " ".join(context.args) if context.args else ""
    if not item:
        await update.message.reply_text(
            "Usage: /promote <backlog item>\n"
            "Example: /promote Learn Spanish"
        )
        return

    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    goals = store.read()

    await update.message.reply_text(f"Classifying '{item}'...")

    try:
        from jacbotcoach.llm.router import LLMRouter
        router = LLMRouter()
        raw = await router.classify_backlog_item(item, goals)
        data = json.loads(raw)
        category = data.get("category", "Short Term Projects")
        difficulty = data.get("difficulty", "Medium")
        rephrased = data.get("rephrased", item)
        notes = data.get("notes", "")
    except Exception as e:
        logger.warning("LLM classification failed (%s), using defaults", e)
        category, difficulty, rephrased, notes = "Short Term Projects", "Medium", item, ""

    if store.promote_backlog_item(item, category, difficulty, rephrased, notes):
        await update.message.reply_text(
            f"✅ Promoted to {category}:\n"
            f"[{difficulty}] {rephrased}"
            + (f"\n{notes}" if notes else "")
        )
    else:
        await update.message.reply_text(
            f"Item not found in backlog: '{item}'\n"
            "Use /status to check the exact text in Open Backlog."
        )
