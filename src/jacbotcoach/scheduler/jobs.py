import logging
from datetime import date, timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from jacbotcoach.config import get_settings
from jacbotcoach.llm.router import LLMRouter
from jacbotcoach.openclaw.client import OpenClawClient
from jacbotcoach.storage.autonomous import AutonomousStore
from jacbotcoach.storage.focus import FocusStore
from jacbotcoach.storage.tasks_log import TasksLog

logger = logging.getLogger(__name__)


async def run_daily_job(application: Application) -> None:
    """
    Daily 8 AM job:
      1. Read goals + focus from storage
      2. Generate 4-5 tasks via desktop GPU (weighted toward focus goals)
      3. Write OpenClaw session prompts
      4. Spawn OpenClaw sessions
      5. Log to tasks-log.md
      6. Send Telegram morning summary
    """
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    focus_store = FocusStore(settings.autonomous_md_path.parent / "focus.md")
    log = TasksLog(settings.tasks_log_path)
    router = LLMRouter()
    claw = OpenClawClient(settings.openclaw_url, settings.openclaw_token)

    content = store.read()
    if store.is_empty():
        logger.info("AUTONOMOUS.md is empty — skipping daily job.")
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text="Daily task generation skipped — no goals set.\nUse /goals to add your goals.",
        )
        return

    # Inject focus context so the LLM weights tasks toward priority goals
    focus_text = focus_store.read()
    planning_context = content
    if focus_text:
        planning_context = f"PRIORITY FOCUS THIS WEEK:\n{focus_text}\n\n---\n\n{content}"

    logger.info("Generating daily tasks via LLM...")
    try:
        tasks = await router.generate_daily_tasks(planning_context)
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

    spawned = []
    failed = []

    for task in tasks:
        try:
            session_prompt = await router.generate_session_prompt(task, content)
            result = await claw.create_session(prompt=session_prompt, label=task[:80])
            await log.append(task, status="SCHEDULED")
            spawned.append((task, result.session_id))
            logger.info("Spawned session %s for: %s", result.session_id, task)
        except Exception as e:
            err_str = str(e)
            logger.error("Failed to spawn session for '%s': %s", task, err_str)
            await log.append(task, status="SPAWN_FAILED")
            failed.append((task, err_str))

    lines = [f"Good morning. {len(spawned)} task(s) queued:\n"]
    if focus_text:
        lines.insert(0, f"🎯 Focus: {focus_text[:100]}\n")
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


async def run_weekly_summary_job(application: Application) -> None:
    """
    Sunday 9 AM job: generate a weekly digest and send via Telegram.
    """
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    focus_store = FocusStore(settings.autonomous_md_path.parent / "focus.md")
    log = TasksLog(settings.tasks_log_path)
    router = LLMRouter()

    goals = store.read()
    if store.is_empty():
        return

    # Collect the past 7 days from the task log
    today = date.today()
    week_lines = []
    for line in log.read_all().splitlines():
        for i in range(7):
            d = (today - timedelta(days=i)).isoformat()
            if d in line:
                week_lines.append(line.strip())
                break

    if not week_lines:
        task_log_text = "No tasks logged this week."
    else:
        task_log_text = "\n".join(week_lines)

    logger.info("Generating weekly summary...")
    try:
        summary = await router.weekly_summary(
            goals=goals,
            task_log=task_log_text,
            focus=focus_store.read(),
        )
    except Exception as e:
        logger.exception("Weekly summary failed")
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text=f"Weekly summary failed: {e}",
        )
        return

    week_label = today.strftime("Week of %B %d")
    await application.bot.send_message(
        chat_id=settings.telegram_allowed_user_id,
        text=f"📊 {week_label}\n\n{summary}",
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

    scheduler.add_job(
        run_weekly_summary_job,
        trigger=CronTrigger(day_of_week="sun", hour=9, minute=0, timezone=tz),
        args=[application],
        id="weekly_summary",
        name="Weekly Summary",
        replace_existing=True,
    )

    logger.info(
        "Scheduler: daily at %02d:%02d, weekly summary Sundays 09:00 (%s)",
        settings.daily_task_hour,
        settings.daily_task_minute,
        settings.timezone,
    )
    return scheduler
