import logging
import re
from telegram import Update
from telegram.ext import ContextTypes

from jacbotcoach.config import get_settings
from jacbotcoach.storage.autonomous import AutonomousStore
from jacbotcoach.storage.tasks_log import TasksLog

logger = logging.getLogger(__name__)


def _is_allowed(update: Update) -> bool:
    settings = get_settings()
    return update.effective_user is not None and (
        update.effective_user.id == settings.telegram_allowed_user_id
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        "JacbotCoach online.\n\n"
        "Goals:\n"
        "  /goals        — brain dump your goals\n"
        "  /goals_list   — view all goals in a flat list\n"
        "  /goals_cat    — view goals by category\n"
        "  /status       — view the full goals file\n\n"
        "Tasks:\n"
        "  /tasks        — view today's task queue\n"
        "  /trigger      — run task generation now\n"
        "  /done <task>  — mark a task complete\n"
        "  /update <msg> — log a progress update\n"
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
            parts = [p.strip() for p in line.strip("|").split("|")]
            if parts and not all(c in "-| " for c in line):
                # Skip header rows (contain "Difficulty", "Frequency", etc.)
                if parts[0] not in ("Difficulty", "Frequency", "Goal", "Habit", "---"):
                    goals.append(parts)

    if not goals:
        await update.message.reply_text(
            "Goals not yet categorized.\nUse /status to see the raw file."
        )
        return

    lines = ["All goals:\n"]
    for i, row in enumerate(goals, 1):
        # row[0]=difficulty/freq, row[1]=goal title, row[2]=notes
        difficulty = row[0] if len(row) > 0 else ""
        title = row[1] if len(row) > 1 else row[0]
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
