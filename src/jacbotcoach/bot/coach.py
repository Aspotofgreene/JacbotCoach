"""
/coach — free-form multi-turn coaching conversation.

The LLM reads your goals for context and responds like a direct,
practical coach. Use /endcoach to exit.
"""

import logging
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from jacbotcoach.bot.formatting import fix_md
from jacbotcoach.config import get_settings
from jacbotcoach.llm.router import LLMRouter
from jacbotcoach.storage.autonomous import AutonomousStore

logger = logging.getLogger(__name__)

COACHING = 0


def _is_allowed(update: Update) -> bool:
    settings = get_settings()
    return update.effective_user is not None and (
        update.effective_user.id == settings.telegram_allowed_user_id
    )


async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_allowed(update):
        return ConversationHandler.END
    context.user_data["coach_history"] = []
    await update.message.reply_text(
        "Coaching session started. Talk to me about your goals, blockers, or anything on your mind.\n\n"
        "Send /endcoach when you're done."
    )
    return COACHING


async def coach_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        return COACHING

    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    goals = store.read() if not store.is_empty() else "No goals set yet."

    history = context.user_data.setdefault("coach_history", [])
    history.append({"role": "user", "content": text})

    await update.message.chat.send_action("typing")

    try:
        router = LLMRouter()
        response = await router.coach_response(
            message=text,
            goals=goals,
            history=history[:-1],  # history before this message
        )
    except Exception as e:
        logger.exception("Coach LLM error")
        await update.message.reply_text(f"LLM error: {e}")
        return COACHING

    history.append({"role": "assistant", "content": response})
    # Keep history bounded to last 10 exchanges
    context.user_data["coach_history"] = history[-20:]

    await update.message.reply_text(fix_md(response), parse_mode="Markdown")
    return COACHING


async def coach_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("coach_history", None)
    await update.message.reply_text(
        "Coaching session ended. Use /coach to start a new one."
    )
    return ConversationHandler.END


coach_conversation = ConversationHandler(
    entry_points=[CommandHandler("coach", coach_start)],
    states={
        COACHING: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, coach_message),
        ],
    },
    fallbacks=[CommandHandler("endcoach", coach_end)],
    name="coach_conversation",
    persistent=False,
)
