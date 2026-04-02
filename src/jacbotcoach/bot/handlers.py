import logging
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
        "Commands:\n"
        "  /goals   — brain dump your goals\n"
        "  /status  — view your current goals\n"
        "  /tasks   — view today's task queue\n"
        "  /trigger — manually kick off today's task generation\n"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    content = store.read()
    if store.is_empty():
        await update.message.reply_text(
            "No goals set yet. Use /goals to add them."
        )
        return
    lines = store.line_count()
    await update.message.reply_text(
        f"Current goals ({lines}/{50} lines):\n\n{content[:4000]}"
    )


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
