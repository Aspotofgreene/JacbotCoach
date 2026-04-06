import logging
import re
from datetime import date, timedelta
from pathlib import Path

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from jacbotcoach.bot.formatting import fix_md
from jacbotcoach.config import get_settings
from jacbotcoach.llm.router import LLMRouter
from jacbotcoach.openclaw.client import OpenClawClient
from jacbotcoach.storage.autonomous import AutonomousStore
from jacbotcoach.storage.focus import FocusStore
from jacbotcoach.storage.draft_queue import DraftQueue
from jacbotcoach.storage.project_queue import ProjectQueue
from jacbotcoach.storage.research_queue import ResearchQueue
from jacbotcoach.storage.tasks_log import TasksLog
from jacbotcoach.storage.accountability import AccountabilityStore
from jacbotcoach.storage.weekly_plan import WeeklyPlanStore

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

    # Ready research reports (spawned topics whose output file now exists)
    research_queue = ResearchQueue(settings.research_queue_path)
    ready_reports = [
        i for i in research_queue.get_all()
        if i.get("status") == "spawned" and Path(i.get("output_path", "")).exists()
    ]
    if ready_reports:
        lines.append(f"\n🔬 Research ready ({len(ready_reports)}):")
        for item in ready_reports:
            lines.append(f"  • {item['topic']}")
            lines.append(f"    {item['output_path']}")

    # Warn about research that was spawned overnight but produced no output
    stale_reports = [
        i for i in research_queue.get_all()
        if i.get("status") == "spawned" and not Path(i.get("output_path", "")).exists()
    ]
    if stale_reports:
        lines.append(f"\n⚠️ Research incomplete — OpenClaw may have failed ({len(stale_reports)}):")
        for item in stale_reports:
            lines.append(f"  • {item['topic']}")
        lines.append("  Use /research_list for details.")

    # Ready drafts (spawned drafts whose output file now exists)
    draft_queue = DraftQueue(settings.draft_queue_path)
    ready_drafts = [
        i for i in draft_queue.get_all()
        if i.get("status") == "spawned" and Path(i.get("output_path", "")).exists()
    ]
    if ready_drafts:
        lines.append(f"\n✍️ Drafts ready ({len(ready_drafts)}):")
        for item in ready_drafts:
            lines.append(f"  • {item['topic']}")
            lines.append(f"    {item['output_path']}")

    # Ready project builds (spawned builds whose output dir now exists)
    project_queue = ProjectQueue(settings.project_queue_path)
    ready_builds = [
        i for i in project_queue.get_all()
        if i.get("status") == "spawned" and Path(i.get("output_path", "")).exists()
    ]
    if ready_builds:
        lines.append(f"\n🔨 Builds ready ({len(ready_builds)}):")
        for item in ready_builds:
            lines.append(f"  • {item['idea']}")
            lines.append(f"    {item['output_path']}")

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
                text=f"⚠️ Goal hasn't seen activity in {settings.stall_days} days:\n*{goal}*\n\n{fix_md(nudge)}",
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
        text=f"📊 {week_label}\n\n{fix_md(summary)}",
        parse_mode="Markdown",
    )


async def run_weekly_planning_job(application: Application) -> None:
    """
    Sunday 6 PM: propose next week's focus based on this week's activity.
    Sends a proposal; user replies /approve to adopt it or /focus <text> to override.
    """
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    focus_store = FocusStore(settings.autonomous_md_path.parent / "focus.md")
    log = TasksLog(settings.tasks_log_path)
    plan_store = WeeklyPlanStore(settings.weekly_plan_path)
    router = LLMRouter()

    if store.is_empty():
        logger.info("Weekly planning: no goals on file — skipping.")
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

    logger.info("Generating weekly plan proposal...")
    try:
        proposal = await router.propose_weekly_plan(
            goals=store.read(),
            task_log=task_log_text,
            focus=focus_store.read(),
        )
    except Exception as e:
        logger.exception("Weekly plan proposal failed")
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text=f"Weekly planning failed: {e}",
        )
        return

    plan_store.set_proposal(proposal)

    await application.bot.send_message(
        chat_id=settings.telegram_allowed_user_id,
        text=(
            "📅 Weekly Planning — proposed focus for next week:\n\n"
            f"{fix_md(proposal)}\n\n"
            "Reply /approve to set this as your weekly focus, "
            "or /focus <text> to write your own."
        ),
        parse_mode="Markdown",
    )


async def run_research_job(application: Application) -> None:
    """
    Midnight job: for each pending topic in the research queue, spawn an
    OpenClaw agent to research it and save a markdown summary to
    research/<date>-<slug>.md.
    """
    settings = get_settings()
    queue = ResearchQueue(settings.research_queue_path)
    claw = OpenClawClient(settings.openclaw_url, settings.openclaw_token)

    pending = queue.get_pending()
    if not pending:
        logger.info("Research job: no pending topics.")
        return

    logger.info("Research job: processing %d topic(s).", len(pending))
    settings.research_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()

    spawned = []
    failed = []

    for item in pending:
        topic = item["topic"]
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:50]
        output_path = settings.research_dir / f"{today_str}-{slug}.md"

        prompt = (
            f"Research the following topic thoroughly and produce a detailed markdown summary.\n\n"
            f"Topic: {topic}\n\n"
            f"Your research should cover:\n"
            f"- Overview and key concepts\n"
            f"- Current state and recent developments\n"
            f"- Key players, tools, or resources\n"
            f"- Practical implications or actionable insights\n"
            f"- Further reading recommendations\n\n"
            f"Save your complete research summary as a markdown file at: {output_path}\n"
            f"The file should start with a # heading and be well-structured with clear sections.\n\n"
            f"When done, append a ✅ line to memory/tasks-log.md in exactly this format:\n"
            f"- [{today_str}] [DONE] ✅ Research complete: {topic}\n\n"
            f"Never edit AUTONOMOUS.md directly."
        )

        try:
            result = await claw.create_session(
                prompt=prompt,
                label=f"research-{slug}"[:80],
            )
            await queue.mark_spawned(topic, result.session_id, str(output_path))
            spawned.append((topic, result.session_id, str(output_path)))
            logger.info("Research spawned session %s for: %s", result.session_id, topic)
        except Exception as e:
            err_str = str(e)
            logger.error("Research spawn failed for '%s': %s", topic, err_str)
            await queue.mark_failed(topic, err_str)
            failed.append((topic, err_str))

    if spawned or failed:
        lines = [f"🔬 Research job: {len(spawned)} topic(s) dispatched.\n"]
        for topic, sid, out in spawned:
            lines.append(f"  • {topic}")
            lines.append(f"    Session: {sid}")
            lines.append(f"    Output: {out}")
        if failed:
            lines.append(f"\n⚠️ {len(failed)} failed to spawn:")
            for topic, err in failed:
                lines.append(f"  • {topic}: {err[:100]}")
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text="\n".join(lines),
        )


async def run_content_drafting_job(application: Application) -> None:
    """
    1 AM job: for each pending topic in the draft queue, spawn an
    OpenClaw agent to write a first-draft document and save it to
    drafts/<date>-<slug>.md.
    """
    settings = get_settings()
    queue = DraftQueue(settings.draft_queue_path)
    claw = OpenClawClient(settings.openclaw_url, settings.openclaw_token)

    pending = queue.get_pending()
    if not pending:
        logger.info("Content drafting job: no pending drafts.")
        return

    logger.info("Content drafting job: processing %d draft(s).", len(pending))
    settings.drafts_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    # Use absolute path so OpenClaw always knows exactly where to write
    drafts_abs = settings.drafts_dir.resolve()

    spawned = []
    failed = []

    for item in pending:
        topic = item["topic"]
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:50]
        output_path = drafts_abs / f"{today_str}-{slug}.md"

        prompt = (
            f"Write a detailed first draft on the following topic and save it as a markdown file.\n\n"
            f"Topic: {topic}\n\n"
            f"Instructions:\n"
            f"1. Write a substantial first draft (800–2000 words) in clear, engaging prose\n"
            f"2. Structure it with:\n"
            f"   - A # title heading at the top\n"
            f"   - An introduction paragraph\n"
            f"   - 3–5 clearly labeled sections (## headings)\n"
            f"   - A conclusion\n"
            f"3. Use a thoughtful, literary tone suited to long-form storytelling or essays\n"
            f"4. Save the complete draft to this EXACT absolute path: {output_path}\n"
            f"   Do NOT save it anywhere else. Create the file at that exact path.\n\n"
            f"When the file is saved, append exactly this line to "
            f"{settings.tasks_log_path.resolve()}:\n"
            f"- [{today_str}] [DONE] ✅ Draft complete: {topic[:80]}\n\n"
            f"Never edit AUTONOMOUS.md directly."
        )

        try:
            result = await claw.create_session(
                prompt=prompt,
                label=f"draft-{slug}"[:80],
            )
            await queue.mark_spawned(topic, result.session_id, str(output_path))
            spawned.append((topic, result.session_id, str(output_path)))
            logger.info("Draft spawned session %s for: %s", result.session_id, topic)
        except Exception as e:
            err_str = str(e)
            logger.error("Draft spawn failed for '%s': %s", topic, err_str)
            await queue.mark_failed(topic, err_str)
            failed.append((topic, err_str))

    if spawned or failed:
        lines = [f"✍️ Content drafting job: {len(spawned)} draft(s) dispatched.\n"]
        for topic, sid, out in spawned:
            lines.append(f"  • {topic}")
            lines.append(f"    Session: {sid}")
            lines.append(f"    Output: {out}")
        if failed:
            lines.append(f"\n⚠️ {len(failed)} failed to spawn:")
            for topic, err in failed:
                lines.append(f"  • {topic}: {err[:100]}")
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text="\n".join(lines),
        )


async def run_project_builder_job(application: Application) -> None:
    """
    2 AM job: for each pending idea in the project queue, spawn an
    OpenClaw agent to scaffold a working prototype in projects/<slug>/.
    """
    settings = get_settings()
    queue = ProjectQueue(settings.project_queue_path)
    store = AutonomousStore(settings.autonomous_md_path)
    claw = OpenClawClient(settings.openclaw_url, settings.openclaw_token)
    router = LLMRouter()

    pending = queue.get_pending()
    if not pending:
        logger.info("Project builder job: no pending ideas.")
        return

    logger.info("Project builder job: processing %d idea(s).", len(pending))
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    goals = store.read() if not store.is_empty() else ""

    spawned = []
    failed = []

    for item in pending:
        idea = item["idea"]
        slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:50]
        output_path = settings.projects_dir / f"{today_str}-{slug}"

        try:
            build_prompt = await router.generate_build_prompt(
                idea=idea,
                goals=goals,
                output_dir=str(output_path),
            )
            result = await claw.create_session(
                prompt=build_prompt,
                label=f"build-{slug}"[:80],
            )
            await queue.mark_spawned(idea, result.session_id, str(output_path))
            spawned.append((idea, result.session_id, str(output_path)))
            logger.info("Build spawned session %s for: %s", result.session_id, idea)
        except Exception as e:
            err_str = str(e)
            logger.error("Build spawn failed for '%s': %s", idea, err_str)
            await queue.mark_failed(idea, err_str)
            failed.append((idea, err_str))

    if spawned or failed:
        lines = [f"🔨 Project builder job: {len(spawned)} project(s) dispatched.\n"]
        for idea, sid, out in spawned:
            lines.append(f"  • {idea}")
            lines.append(f"    Session: {sid}")
            lines.append(f"    Output: {out}")
        if failed:
            lines.append(f"\n⚠️ {len(failed)} failed to spawn:")
            for idea, err in failed:
                lines.append(f"  • {idea}: {err[:100]}")
        await application.bot.send_message(
            chat_id=settings.telegram_allowed_user_id,
            text="\n".join(lines),
        )


async def run_accountability_scoring_job(application: Application) -> None:
    """
    Sunday 7 PM: compute a weekly 1-10 score across consistency, focus alignment,
    and momentum; save to memory/accountability-scores.json and send a Telegram summary.
    """
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    focus_store = FocusStore(settings.autonomous_md_path.parent / "focus.md")
    log = TasksLog(settings.tasks_log_path)
    scores_store = AccountabilityStore(settings.accountability_scores_path)
    router = LLMRouter()

    if store.is_empty():
        logger.info("Accountability scoring: no goals on file — skipping.")
        return

    today = date.today()
    week_ending = today.isoformat()

    # Collect this week's log lines (past 7 days)
    done_lines: list[str] = []
    scheduled_count = 0
    for line in log.read_all().splitlines():
        line = line.strip()
        for i in range(7):
            d = (today - timedelta(days=i)).isoformat()
            if d in line:
                if "[DONE]" in line:
                    done_lines.append(line)
                elif "[SCHEDULED]" in line:
                    scheduled_count += 1
                break

    done_count = len(done_lines)

    # --- Consistency (1-10): tasks completed vs tasks scheduled ---
    if scheduled_count == 0:
        # Nothing was even scheduled — score reflects that
        consistency = 5 if done_count > 0 else 3
    else:
        ratio = done_count / scheduled_count
        consistency = max(1, min(10, round(ratio * 10)))

    # --- Focus Alignment (1-10): do completed tasks mention focus keywords? ---
    focus_text = focus_store.read()
    if not focus_text:
        focus_alignment = 5  # neutral when no focus is set
    elif done_count == 0:
        focus_alignment = 1
    else:
        focus_words = set(
            w.lower() for w in re.split(r"\W+", focus_text) if len(w) > 3
        )
        aligned = sum(
            1 for line in done_lines
            if any(w in line.lower() for w in focus_words)
        )
        ratio = aligned / done_count
        focus_alignment = max(1, min(10, round(ratio * 10)))
        # Partial credit floor: if you did tasks at all, give at least 3
        if done_count > 0 and focus_alignment < 3:
            focus_alignment = 3

    # --- Momentum (1-10): this week vs last week's done_count ---
    prev_done = scores_store.get_previous_done_count(week_ending)
    if prev_done == 0 and done_count == 0:
        momentum = 5
    elif prev_done == 0:
        momentum = 7  # first week with data — neutral-positive
    else:
        ratio = done_count / prev_done
        # >1.0 means improvement, <1.0 means drop
        if ratio >= 1.5:
            momentum = 10
        elif ratio >= 1.2:
            momentum = 9
        elif ratio >= 1.0:
            momentum = 7
        elif ratio >= 0.8:
            momentum = 5
        elif ratio >= 0.5:
            momentum = 3
        else:
            momentum = 1

    logger.info(
        "Accountability scoring: week=%s done=%d scheduled=%d "
        "consistency=%d focus=%d momentum=%d",
        week_ending, done_count, scheduled_count,
        consistency, focus_alignment, momentum,
    )

    # --- LLM insight ---
    goals = store.read()
    try:
        insight = await router.accountability_insight(
            week_ending=week_ending,
            consistency=consistency,
            focus_alignment=focus_alignment,
            momentum=momentum,
            done_count=done_count,
            scheduled_count=scheduled_count,
            focus=focus_text,
            goals=goals,
        )
    except Exception:
        logger.exception("Accountability insight LLM call failed — using fallback text")
        insight = "LLM insight unavailable this week."

    # --- Persist ---
    scores_store.record(
        week_ending=week_ending,
        consistency=consistency,
        focus_alignment=focus_alignment,
        momentum=momentum,
        done_count=done_count,
        scheduled_count=scheduled_count,
        insight=insight,
    )

    overall = round((consistency + focus_alignment + momentum) / 3, 1)

    def _bar(score: int) -> str:
        filled = round(score / 2)  # 5-char bar
        return "█" * filled + "░" * (5 - filled)

    text = (
        f"📊 Weekly Accountability Score — {today.strftime('%B %d, %Y')}\n\n"
        f"Consistency     {_bar(consistency)} {consistency}/10\n"
        f"Focus Alignment {_bar(focus_alignment)} {focus_alignment}/10\n"
        f"Momentum        {_bar(momentum)} {momentum}/10\n\n"
        f"Overall: {overall}/10\n\n"
        f"💬 {fix_md(insight.strip())}"
    )

    await application.bot.send_message(
        chat_id=settings.telegram_allowed_user_id,
        text=text,
        parse_mode="Markdown",
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

    scheduler.add_job(
        run_research_job,
        trigger=CronTrigger(hour=0, minute=0, timezone=tz),
        args=[application],
        id="research",
        name="Research Queue",
        replace_existing=True,
    )

    scheduler.add_job(
        run_content_drafting_job,
        trigger=CronTrigger(hour=1, minute=0, timezone=tz),
        args=[application],
        id="content_drafting",
        name="Content Drafting",
        replace_existing=True,
    )

    scheduler.add_job(
        run_project_builder_job,
        trigger=CronTrigger(hour=settings.project_builder_hour, minute=0, timezone=tz),
        args=[application],
        id="project_builder",
        name="Overnight Project Builder",
        replace_existing=True,
    )

    scheduler.add_job(
        run_weekly_planning_job,
        trigger=CronTrigger(
            day_of_week="sun", hour=settings.weekly_planning_hour, minute=0, timezone=tz
        ),
        args=[application],
        id="weekly_planning",
        name="Weekly Planning Session",
        replace_existing=True,
    )

    scheduler.add_job(
        run_accountability_scoring_job,
        trigger=CronTrigger(
            day_of_week="sun", hour=settings.accountability_scoring_hour, minute=0, timezone=tz
        ),
        args=[application],
        id="accountability_scoring",
        name="Accountability Scoring",
        replace_existing=True,
    )

    logger.info(
        "Scheduler: brief %02d:00, tasks %02d:%02d, reflection %02d:00, "
        "stall Mon 07:00, summary Sun 09:00, planning Sun %02d:00, "
        "accountability Sun %02d:00, research 00:00, drafting 01:00, "
        "project builder %02d:00 (%s)",
        settings.morning_brief_hour,
        settings.daily_task_hour,
        settings.daily_task_minute,
        settings.evening_reflection_hour,
        settings.weekly_planning_hour,
        settings.accountability_scoring_hour,
        settings.project_builder_hour,
        settings.timezone,
    )
    return scheduler
