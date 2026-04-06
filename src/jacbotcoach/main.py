import asyncio
import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from jacbotcoach.bot.callbacks import goal_callback
from jacbotcoach.bot.coach import coach_conversation
from jacbotcoach.bot.conversations import goals_conversation
from jacbotcoach.bot.handlers import (
    approve_command,
    deliverables_command,
    done_command,
    done_d_command,
    obstacle_command,
    build_command,
    builds_command,
    draft_command,
    drafts_command,
    focus_command,
    goal_delete_command,
    goal_done_command,
    goal_edit_command,
    goal_status_command,
    goals_categories_command,
    goals_list_command,
    goals_reparse_command,
    help_command,
    history_command,
    idea_command,
    ideas_command,
    milestone_command,
    milestone_done_command,
    milestones_command,
    nl_handler,
    promote_command,
    promote_idea_command,
    research_command,
    research_list_command,
    score_command,
    scores_command,
    status_command,
    streak_done_command,
    streaks_command,
    tasks_command,
    trigger_command,
    unfocus_command,
    update_command,
)
from jacbotcoach.config import get_settings
from jacbotcoach.llm.client import OllamaClient
from jacbotcoach.openclaw.client import OpenClawClient
from jacbotcoach.scheduler.jobs import build_scheduler
from jacbotcoach.watcher import watch_tasks_log

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _post_init(application: Application) -> None:
    """
    Called by python-telegram-bot after it starts its internal event loop.
    Safe place to run async startup tasks and start the scheduler.
    """
    settings = application.bot_data["settings"]

    mac_ok = await OllamaClient(settings.ollama_mac_url, settings.ollama_mac_model).health_check()
    desktop_ok = await OllamaClient(
        settings.ollama_desktop_url, settings.ollama_desktop_model
    ).health_check()
    claw_ok = await OpenClawClient(settings.openclaw_url, settings.openclaw_token).health_check()

    logger.info("Ollama Mac mini  (%s): %s", settings.ollama_mac_url, "OK" if mac_ok else "UNREACHABLE")
    logger.info("Ollama Desktop   (%s): %s", settings.ollama_desktop_url, "OK" if desktop_ok else "UNREACHABLE")
    logger.info("OpenClaw         (%s): %s", settings.openclaw_url, "OK" if claw_ok else "UNREACHABLE")

    if not mac_ok:
        logger.warning("Mac mini Ollama is unreachable — LLM calls will fail!")
    if not desktop_ok:
        logger.warning("Desktop Ollama unreachable — heavy tasks will fall back to Mac mini.")

    scheduler = build_scheduler(application)
    scheduler.start()
    application.bot_data["scheduler"] = scheduler

    # Background task to watch tasks-log.md for completions
    watcher_task = asyncio.create_task(
        watch_tasks_log(
            settings.tasks_log_path,
            application.bot,
            settings.telegram_allowed_user_id,
        )
    )
    application.bot_data["watcher_task"] = watcher_task
    logger.info("JacbotCoach ready.")


async def _post_shutdown(application: Application) -> None:
    scheduler = application.bot_data.get("scheduler")
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    watcher_task = application.bot_data.get("watcher_task")
    if watcher_task and not watcher_task.done():
        watcher_task.cancel()


def main_sync() -> None:
    settings = get_settings()

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["settings"] = settings

    # Core
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("start", help_command))  # Telegram convention alias
    app.add_handler(CommandHandler("status", status_command))

    # Goals — ConversationHandler must come before individual command handlers
    # that share command names (e.g. /done inside the conversation)
    app.add_handler(goals_conversation)
    app.add_handler(coach_conversation)

    app.add_handler(CommandHandler("goals_list", goals_list_command))
    app.add_handler(CommandHandler("goals_cat", goals_categories_command))
    app.add_handler(CommandHandler("goals_reparse", goals_reparse_command))
    app.add_handler(CommandHandler("goal_done", goal_done_command))
    app.add_handler(CommandHandler("goal_status", goal_status_command))
    app.add_handler(CommandHandler("goal_edit", goal_edit_command))
    app.add_handler(CommandHandler("goal_delete", goal_delete_command))
    app.add_handler(CommandHandler("promote", promote_command))
    app.add_handler(CommandHandler("history", history_command))

    # Milestones
    app.add_handler(CommandHandler("milestone", milestone_command))
    app.add_handler(CommandHandler("milestone_done", milestone_done_command))
    app.add_handler(CommandHandler("milestones", milestones_command))

    # Streaks
    app.add_handler(CommandHandler("streaks", streaks_command))
    app.add_handler(CommandHandler("streak_done", streak_done_command))

    # Focus
    app.add_handler(CommandHandler("focus", focus_command))
    app.add_handler(CommandHandler("unfocus", unfocus_command))
    app.add_handler(CommandHandler("approve", approve_command))

    # Tasks
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("trigger", trigger_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("update", update_command))

    # Ideas
    app.add_handler(CommandHandler("idea", idea_command))
    app.add_handler(CommandHandler("ideas", ideas_command))
    app.add_handler(CommandHandler("promote_idea", promote_idea_command))

    # Research
    app.add_handler(CommandHandler("research", research_command))
    app.add_handler(CommandHandler("research_list", research_list_command))

    # Drafts
    app.add_handler(CommandHandler("draft", draft_command))
    app.add_handler(CommandHandler("drafts", drafts_command))

    # Project Builder
    app.add_handler(CommandHandler("build", build_command))
    app.add_handler(CommandHandler("builds", builds_command))

    # Accountability Scoring
    app.add_handler(CommandHandler("score", score_command))
    app.add_handler(CommandHandler("scores", scores_command))

    # Daily Check-Ins
    app.add_handler(CommandHandler("deliverables", deliverables_command))
    app.add_handler(CommandHandler("done_d", done_d_command))
    app.add_handler(CommandHandler("obstacle", obstacle_command))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(goal_callback, pattern=r"^goal:"))

    # Natural language fallback — must be last (lowest priority)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, nl_handler))

    logger.info("Starting JacbotCoach (polling)...")
    # run_polling() manages its own event loop — do NOT wrap in asyncio.run()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main_sync()
