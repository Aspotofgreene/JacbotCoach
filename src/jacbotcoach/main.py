import logging

from telegram.ext import Application, CommandHandler

from jacbotcoach.bot.conversations import goals_conversation
from jacbotcoach.bot.handlers import (
    start_command,
    status_command,
    tasks_command,
    trigger_command,
)
from jacbotcoach.config import get_settings
from jacbotcoach.llm.client import OllamaClient
from jacbotcoach.openclaw.client import OpenClawClient
from jacbotcoach.scheduler.jobs import build_scheduler

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
    logger.info("JacbotCoach ready.")


async def _post_shutdown(application: Application) -> None:
    scheduler = application.bot_data.get("scheduler")
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


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

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("trigger", trigger_command))
    app.add_handler(goals_conversation)

    logger.info("Starting JacbotCoach (polling)...")
    # run_polling() manages its own event loop — do NOT wrap in asyncio.run()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main_sync()
