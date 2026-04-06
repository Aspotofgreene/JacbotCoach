"""
/coach — free-form multi-turn coaching conversation.

The LLM reads your goals and recent coaching notes for context and responds
like a direct, practical coach. Use /endcoach to exit — the session is
automatically summarized and saved to memory/coach-notes.md.
"""

import logging
from datetime import date

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
from jacbotcoach.storage.coach_notes import CoachNotesStore

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

    settings = get_settings()
    notes_store = CoachNotesStore(settings.coach_notes_path)

    context.user_data["coach_history"] = []
    context.user_data["coach_prior_notes"] = notes_store.read_recent(n=3)

    if context.user_data["coach_prior_notes"]:
        await update.message.reply_text(
            "Coaching session started. I have your recent notes for context.\n\n"
            "Talk to me about your goals, blockers, or anything on your mind.\n"
            "Send /endcoach when you're done — I'll save a summary."
        )
    else:
        await update.message.reply_text(
            "Coaching session started. Talk to me about your goals, blockers, or anything on your mind.\n\n"
            "Send /endcoach when you're done — I'll save a summary."
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
    prior_notes = context.user_data.get("coach_prior_notes", "")
    history.append({"role": "user", "content": text})

    await update.message.chat.send_action("typing")

    try:
        router = LLMRouter()
        response = await router.coach_response(
            message=text,
            goals=goals,
            history=history[:-1],  # history before this message
            prior_notes=prior_notes,
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
    history = context.user_data.pop("coach_history", [])
    context.user_data.pop("coach_prior_notes", None)

    # Only summarize if there was a real conversation (at least 1 user message)
    user_turns = [m for m in history if m["role"] == "user"]
    if user_turns:
        await update.message.reply_text("Saving session summary…")
        try:
            settings = get_settings()
            store = AutonomousStore(settings.autonomous_md_path)
            goals = store.read() if not store.is_empty() else "No goals set yet."

            router = LLMRouter()
            summary = await router.summarize_coaching_session(history, goals)

            notes_store = CoachNotesStore(settings.coach_notes_path)
            date_str = date.today().strftime("%Y-%m-%d")
            notes_store.append(date_str, summary)

            await update.message.reply_text(
                f"Session saved to coach notes ({date_str}).\n\n"
                f"{fix_md(summary)}\n\n"
                "Use /coach_notes to review past sessions.",
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Failed to save coaching session summary")
            await update.message.reply_text(
                "Coaching session ended. (Summary could not be saved — check logs.)"
            )
    else:
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
