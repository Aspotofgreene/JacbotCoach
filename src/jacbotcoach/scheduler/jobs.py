import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from jacbotcoach.config import get_settings
from jacbotcoach.llm.router import LLMRouter
from jacbotcoach.openclaw.client import OpenClawClient
from jacbotcoach.storage.autonomous import AutonomousStore
from jacbotcoach.storage.tasks_log import TasksLog

logger = logging.getLogger(__name__)


async def run_daily_job(application: Application) -> None:
    """
    Core daily job:
      1. Read goals from AUTONOMOUS.md
      2. Generate 4-5 tasks via desktop GPU
      3. Write a detailed OpenClaw session prompt for each task
      4. Spawn an OpenClaw session per task
      5. Log each task to tasks-log.md
      6. Send a Telegram summary to the user
    """
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    log = TasksLog(settings.tasks_log_path)
    router = LLMRouter()
    claw = OpenClawClient(settings.openclaw_url, settings.openclaw_token)

    # --- Step 1: Read goals ---
    content = store.read()
    if store.is_empty():
        logger.info("AUTONOMOUS.md is empty — skipping daily job.")
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text=(
                "Daily task generation skipped — no goals set.\n"
                "Use /goals to add your goals."
            ),
        )
        return

    # --- Step 2: Generate tasks ---
    logger.info("Generating daily tasks via LLM...")
    try:
        tasks = await router.generate_daily_tasks(content)
    except Exception as e:
        logger.exception("Task generation failed")
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text=f"Daily task generation failed: {e}",
        )
        return

    if not tasks:
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text="LLM returned no tasks today. Try /trigger again.",
        )
        return

    # --- Steps 3–5: Spawn sessions ---
    spawned = []
    failed = []

    for task in tasks:
        try:
            session_prompt = await router.generate_session_prompt(task, content)
            result = await claw.create_session(
                prompt=session_prompt,
                label=task[:80],
            )
            await log.append(task, status="SCHEDULED")
            spawned.append((task, result.session_id))
            logger.info("Spawned session %s for: %s", result.session_id, task)
        except Exception as e:
            err_str = str(e)
            logger.error("Failed to spawn session for '%s': %s", task, err_str)
            await log.append(task, status="SPAWN_FAILED")
            failed.append((task, err_str))

    # --- Step 6: Telegram notification ---
    lines = [f"Good morning. {len(spawned)} task(s) queued:\n"]
    for i, (task, sid) in enumerate(spawned, 1):
        lines.append(f"{i}. {task}")
        lines.append(f"   Session: {sid}")
    if failed:
        lines.append(f"\n⚠️ {len(failed)} task(s) failed to spawn:")
        for task, err in failed:
            lines.append(f"   - {task}")
            lines.append(f"     Error: {err[:120]}")

    await application.bot.send_message(
        chat_id=settings.telegram_allowed_user_id,
        text="\n".join(lines),
    )


def build_scheduler(application: Application) -> AsyncIOScheduler:
    settings = get_settings()
    tz = pytz.timezone(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        run_daily_job,
        trigger=CronTrigger(
            hour=settings.daily_task_hour,
            minute=settings.daily_task_minute,
            timezone=tz,
        ),
        args=[application],
        id="daily_tasks",
        name="Daily Task Generation",
        replace_existing=True,
    )
    logger.info(
        "Scheduler configured: daily at %02d:%02d %s",
        settings.daily_task_hour,
        settings.daily_task_minute,
        settings.timezone,
    )
    return scheduler
