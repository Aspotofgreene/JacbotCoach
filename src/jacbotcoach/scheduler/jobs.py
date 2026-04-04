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

# Smart scheduling: task count by weekday (0=Mon … 6=Sun)
_TASK_COUNT_BY_DOW = {0: 5, 1: 4, 2: 4, 3: 4, 4: 3, 5: 2, 6: 2}


def _smart_task_count() -> int:
    return _TASK_COUNT_BY_DOW[date.today().weekday()]


async def run_morning_brief_job(application: Application) -> None:
    """
    6 AM briefing: goal status snapshot, focus, yesterday's completions.
    Does NOT spawn tasks — that happens at 8 AM.
    """
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    focus_store = FocusStore(settings.autonomous_md_path.parent / "focus.md")
    log = TasksLog(settings.tasks_log_path)

    if store.is_empty():
        return

    content = store.read()

    # Count goal statuses
    status_counts: dict[str, int] = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("|") and "|" in line[1:] and not all(c in "-| " for c in line):
            parts = [p.strip() for p in line.strip("|").split("|")]
            if parts and parts[0] not in ("Status", "Difficulty", "Frequency", "Goal", "Habit"):
                s = parts[0]
                status_counts[s] = status_counts.get(s, 0) + 1

    # Yesterday's completions
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    done_yesterday = [
        line.strip() for line in log.read_all().splitlines()
        if yesterday in line and "[DONE]" in line and line.strip().startswith("-")
    ]

    lines = [f"Good morning! Here's your {date.today().strftime('%A')} briefing.\n"]

    focus_text = focus_store.read()
    if focus_text:
        lines.append(f"🎯 This week's focus: {focus_text[:120]}\n")

    if status_counts:
        summary = ", ".join(f"{v} {k}" for k, v in status_counts.items())
        lines.append(f"📋 Goals: {summary}")

    if done_yesterday:
        lines.append(f"\n✅ Completed yesterday ({len(done_yesterday)}):")
        for entry in done_yesterday[:5]:
            # Strip the log prefix for readability
            import re
            clean = re.sub(r"^\s*-\s*\[\d{4}-\d{2}-\d{2}\]\s*\[\w+\]\s*[✅📝🔄]?\s*", "", entry)
            lines.append(f"  • {clean.strip()}")

    target = _smart_task_count()
    lines.append(f"\nTasks will be generated at {settings.daily_task_hour:02d}:00 ({target} tasks planned for today).")

    await application.bot.send_message(
        chat_id=settings.telegram_allowed_user_id,
        text="\n".join(lines),
    )


async def run_daily_job(application: Application) -> None:
    """
    Daily 8 AM job:
      1. Read goals + focus from storage
      2. Generate tasks via desktop GPU (count based on day of week)
      3. Write OpenClaw session prompts
      4. Spawn OpenClaw sessions
      5. Log to tasks-log.md
      6. Send Telegram summary
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

    focus_text = focus_store.read()
    planning_context = content
    if focus_text:
        planning_context = f"PRIORITY FOCUS THIS WEEK:\n{focus_text}\n\n---\n\n{content}"

    target_count = _smart_task_count()
    day_name = date.today().strftime("%A")
    logger.info("Generating %d tasks for %s via LLM...", target_count, day_name)

    try:
        tasks = await router.generate_daily_tasks(planning_context, count=target_count)
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

    lines = [f"{len(spawned)} task(s) queued for {day_name}:\n"]
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


async def run_evening_reflection_job(application: Application) -> None:
    """8 PM prompt: ask the user to log anything they accomplished manually."""
    settings = get_settings()
    log = TasksLog(settings.tasks_log_path)
    today_tasks = log.read_today()

    done_count = sum(1 for t in today_tasks if "[DONE]" in t)
    total_count = sum(1 for t in today_tasks if "[SCHEDULED]" in t)

    lines = ["🌙 Evening check-in.\n"]
    if total_count:
        lines.append(f"Today: {done_count}/{total_count} scheduled tasks completed.")
    lines.append(
        "\nDid you accomplish anything not on the task list? "
        "Log it with /done <description> so it counts toward your weekly summary."
    )
    lines.append("\nWhat's on your mind for tomorrow? Use /focus to set priorities.")

    await application.bot.send_message(
        chat_id=settings.telegram_allowed_user_id,
        text="\n".join(lines),
    )


async def run_stall_detection_job(application: Application) -> None:
    """
    Monday 7 AM: check each active goal against the task log.
    Send a nudge for any goal with no related activity in the past stall_days.
    """
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    log = TasksLog(settings.tasks_log_path)
    router = LLMRouter()

    if store.is_empty():
        return

    content = store.read()
    cutoff = (date.today() - timedelta(days=settings.stall_days)).isoformat()
    recent_log = "\n".join(
        line for line in log.read_all().splitlines()
        if line.strip() and line.strip()[3:13] >= cutoff  # date portion of log line
    )

    # Extract active goals
    active_goals = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("|") and "|" in line[1:] and not all(c in "-| " for c in line):
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) >= 3 and parts[0] in ("Active", "In Progress"):
                active_goals.append(parts[2])  # goal title column

    stalled = []
    for goal in active_goals:
        # Simple keyword check — if goal title words appear nowhere in recent log
        keywords = [w for w in goal.lower().split() if len(w) > 4]
        if keywords and not any(
            any(kw in line.lower() for kw in keywords)
            for line in recent_log.splitlines()
        ):
            stalled.append(goal)

    if not stalled:
        logger.info("Stall detection: no stalled goals found.")
        return

    logger.info("Stall detection: %d stalled goal(s)", len(stalled))
    for goal in stalled[:3]:  # cap at 3 nudges per week
        try:
            nudge = await router.stall_nudge(goal, settings.stall_days)
            await application.bot.send_message(
                chat_id=settings.telegram_allowed_user_id,
                text=f"⚠️ Goal hasn't seen activity in {settings.stall_days} days:\n*{goal}*\n\n{nudge}",
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Stall nudge failed for goal: %s", goal)


async def run_weekly_summary_job(application: Application) -> None:
    """Sunday 9 AM: generate a weekly digest and send via Telegram."""
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    focus_store = FocusStore(settings.autonomous_md_path.parent / "focus.md")
    log = TasksLog(settings.tasks_log_path)
    router = LLMRouter()

    goals = store.read()
    if store.is_empty():
        return

    today = date.today()
    week_lines = []
    for line in log.read_all().splitlines():
        for i in range(7):
            d = (today - timedelta(days=i)).isoformat()
            if d in line:
                week_lines.append(line.strip())
                break

    task_log_text = "\n".join(week_lines) if week_lines else "No tasks logged this week."

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
        run_morning_brief_job,
        trigger=CronTrigger(hour=settings.morning_brief_hour, minute=0, timezone=tz),
        args=[application],
        id="morning_brief",
        name="Morning Briefing",
        replace_existing=True,
    )

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
        run_evening_reflection_job,
        trigger=CronTrigger(hour=settings.evening_reflection_hour, minute=0, timezone=tz),
        args=[application],
        id="evening_reflection",
        name="Evening Reflection",
        replace_existing=True,
    )

    scheduler.add_job(
        run_stall_detection_job,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=tz),
        args=[application],
        id="stall_detection",
        name="Stall Detection",
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
        "Scheduler: brief %02d:00, tasks %02d:%02d, reflection %02d:00, "
        "stall Mon 07:00, summary Sun 09:00 (%s)",
        settings.morning_brief_hour,
        settings.daily_task_hour,
        settings.daily_task_minute,
        settings.evening_reflection_hour,
        settings.timezone,
    )
    return scheduler
