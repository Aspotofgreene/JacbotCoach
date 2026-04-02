import logging
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from jacbotcoach.config import get_settings
from jacbotcoach.storage.autonomous import AutonomousStore

logger = logging.getLogger(__name__)

COLLECTING, CONFIRMING = range(2)


def _is_allowed(update: Update) -> bool:
    settings = get_settings()
    return update.effective_user is not None and (
        update.effective_user.id == settings.telegram_allowed_user_id
    )


async def goals_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_allowed(update):
        return ConversationHandler.END
    context.user_data["goal_buffer"] = []
    await update.message.reply_text(
        "Brain dump your goals. Send one per message or all at once.\n\n"
        "When you're done: /done\n"
        "To cancel: /cancel"
    )
    return COLLECTING


async def goals_collect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        return COLLECTING
    context.user_data.setdefault("goal_buffer", []).append(text)
    count = len(context.user_data["goal_buffer"])
    await update.message.reply_text(
        f"Got it. ({count} message{'s' if count > 1 else ''} captured)\n"
        "Keep going or send /done to finish."
    )
    return COLLECTING


async def goals_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    goals = context.user_data.get("goal_buffer", [])
    if not goals:
        await update.message.reply_text(
            "Nothing captured yet. Send some goals or /cancel."
        )
        return COLLECTING

    combined = "\n\n".join(goals)
    preview = combined[:2000] + ("..." if len(combined) > 2000 else "")
    await update.message.reply_text(
        f"Here's what I captured:\n\n{preview}\n\n"
        "Send /confirm to save, or /cancel to discard."
    )
    context.user_data["goal_combined"] = combined
    return CONFIRMING


async def goals_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    combined = context.user_data.get("goal_combined", "")
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)

    # Use LLM to structure the raw brain dump into clean markdown
    try:
        from jacbotcoach.llm.router import LLMRouter
        router = LLMRouter()
        await update.message.reply_text(
            "Structuring your goals via AI... (this may take 20-30s)"
        )
        structured = await router.parse_goals(combined)
    except Exception as e:
        logger.warning(f"LLM structuring failed, saving raw: {e}")
        structured = _format_raw_goals(combined)

    try:
        store.write(structured)
        lines = store.line_count()
        await update.message.reply_text(
            f"Goals saved to AUTONOMOUS.md ({lines} lines).\n\n"
            "The scheduler will use these tomorrow at 8 AM.\n"
            "Use /trigger to run task generation right now."
        )
    except ValueError as e:
        await update.message.reply_text(
            f"Could not save: {e}\n\n"
            "Try breaking your goals into fewer, shorter items."
        )

    context.user_data.clear()
    return ConversationHandler.END


async def goals_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Your goals were not changed.")
    return ConversationHandler.END


def _format_raw_goals(raw: str) -> str:
    """Fallback formatter when LLM is unavailable."""
    lines = ["# Goals", "", "## Active Goals"]
    for line in raw.splitlines():
        line = line.strip()
        if line:
            lines.append(f"- {line}" if not line.startswith("-") else line)
    return "\n".join(lines) + "\n"


goals_conversation = ConversationHandler(
    entry_points=[CommandHandler("goals", goals_start)],
    states={
        COLLECTING: [
            CommandHandler("done", goals_done),
            MessageHandler(filters.TEXT & ~filters.COMMAND, goals_collect),
        ],
        CONFIRMING: [
            CommandHandler("confirm", goals_confirm),
        ],
    },
    fallbacks=[CommandHandler("cancel", goals_cancel)],
    name="goals_conversation",
    persistent=False,
)
