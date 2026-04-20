import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from jacbotcoach.config import get_settings
from jacbotcoach.storage.accountability import AccountabilityStore
from jacbotcoach.storage.autonomous import AutonomousStore
from jacbotcoach.storage.coach_notes import CoachNotesStore
from jacbotcoach.storage.focus import FocusStore
from jacbotcoach.storage.milestones import MilestoneStore
from jacbotcoach.storage.streaks import StreakStore
from jacbotcoach.storage.tasks_log import TasksLog

logger = logging.getLogger(__name__)


def _is_allowed(update: Update) -> bool:
    settings = get_settings()
    user = update.effective_user
    if user is None:
        logger.warning("Update has no effective_user — ignoring")
        return False
    allowed = user.id == settings.telegram_allowed_user_id
    if not allowed:
        logger.warning(
            "Rejected message from user_id=%s (allowed=%s)",
            user.id,
            settings.telegram_allowed_user_id,
        )
    return allowed


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("help_command received from user_id=%s", update.effective_user and update.effective_user.id)
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        "JacbotCoach online.\n\n"
        "Goals:\n"
        "  /goals           — brain dump your goals (then /save, /confirm)\n"
        "  /goals_list      — view all goals with action buttons\n"
        "  /goals_cat       — view goals by category\n"
        "  /goals_reparse   — re-structure goals through AI (fixes raw text)\n"
        "  /goal_done <title> — mark a goal complete\n"
        "  /goal_status <title> <status> — update goal status\n"
        "  /goal_edit <old> | <new> — rename a goal\n"
        "  /goal_delete <title> — remove a goal\n"
        "  /promote <item>  — move backlog item to active goals\n"
        "  /history <title> — see task history for a goal\n"
        "  /status          — view the full goals file\n\n"
        "Milestones:\n"
        "  /milestone <goal> | <step> — add a milestone to a goal\n"
        "  /milestone_done <goal> | <step> — mark a milestone complete\n"
        "  /milestones <goal> — show milestones for a goal\n\n"
        "Streaks:\n"
        "  /streaks         — view habit streaks\n"
        "  /streak_done <habit> — log a habit completion\n\n"
        "Focus:\n"
        "  /focus           — see current focus goals\n"
        "  /focus <goals>   — set weekly focus goals\n"
        "  /unfocus         — clear focus\n"
        "  /approve         — accept the bot's Sunday evening plan proposal\n\n"
        "Tasks:\n"
        "  /tasks           — view today's tasks\n"
        "  /trigger         — run task generation now\n"
        "  /done <task>     — mark a task complete\n"
        "  /update <msg>    — log a progress update\n\n"
        "Coaching:\n"
        "  /coach           — start a coaching conversation\n"
        "  /endcoach        — end coaching session (auto-saves summary)\n"
        "  /coach_notes     — review saved coaching session summaries\n\n"
        "Ideas:\n"
        "  /idea <text>       — capture a quick idea instantly\n"
        "  /ideas             — list all captured ideas\n"
        "  /promote_idea <text> — move an idea to the Open Backlog\n\n"
        "Research:\n"
        "  /research <topic>  — queue a topic for overnight research\n"
        "  /research_trigger  — run research job now\n"
        "  /research_retry    — reset stuck research and re-run now\n"
        "  /research_list     — view queue and ready reports\n\n"
        "Drafts:\n"
        "  /draft <topic>     — queue a first-draft writing session\n"
        "  /draft_trigger     — run drafting job now (don't wait for 1 AM)\n"
        "  /draft_retry       — reset stuck drafts and re-run them now\n"
        "  /drafts            — view draft queue and completed drafts\n\n"
        "Builds:\n"
        "  /build <idea>      — queue overnight prototype scaffolding by OpenClaw\n"
        "  /builds            — view build queue and completed prototypes\n\n"
        "Accountability:\n"
        "  /score             — view this week's accountability score (1-10)\n"
        "  /scores            — view score history across all tracked weeks\n\n"
        "Tip: You can also just type naturally, e.g. 'mark Learn Spanish as done'."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.is_empty():
        await update.message.reply_text("No goals set yet. Use /goals to add them.")
        return
    content = store.read()
    lines = store.line_count()
    await update.message.reply_text(
        f"Goals file ({lines}/50 lines):\n\n{content[:4000]}"
    )


async def goals_reparse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-run LLM structuring on whatever is currently in AUTONOMOUS.md."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.is_empty():
        await update.message.reply_text("No goals found. Use /goals to add them first.")
        return
    raw = store.read()
    await update.message.reply_text("Re-structuring your goals via AI… (this may take 20-30s)")
    try:
        from jacbotcoach.llm.router import LLMRouter
        structured = await LLMRouter().parse_goals(raw)
        store.write(structured)
        lines = store.line_count()
        await update.message.reply_text(
            f"Done. Goals re-structured and saved ({lines} lines).\n\nUse /goals_list or /goals_cat to review."
        )
    except Exception as e:
        logger.error("goals_reparse failed: %s", e)
        await update.message.reply_text(f"Failed to re-parse goals: {e}")


def _extract_goal_rows(content: str) -> list[dict]:
    """Extract goal rows from AUTONOMOUS.md as dicts with status/difficulty/title."""
    goals = []
    for line in content.splitlines():
        line = line.strip()
        if not (line.startswith("|") and "|" in line[1:]):
            continue
        if all(c in "-| " for c in line):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 3:
            continue
        if parts[0] in ("Status", "Difficulty", "Frequency", "Goal", "Habit"):
            continue
        goals.append({"status": parts[0], "difficulty": parts[1], "title": parts[2]})
    return goals


async def goals_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all goals as a numbered list with inline action buttons."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.is_empty():
        await update.message.reply_text("No goals yet. Use /goals to add some.")
        return

    goals = _extract_goal_rows(store.read())
    if not goals:
        await update.message.reply_text("Goals not yet categorized.\nUse /status to see the raw file.")
        return

    # Send each goal as its own message with action buttons (Telegram 64-byte callback limit)
    intro = f"Your goals ({len(goals)} total):"
    await update.message.reply_text(intro)

    for i, goal in enumerate(goals):
        label = f"{i + 1}. [{goal['difficulty']}] {goal['title']}"
        status = goal["status"]
        # Build context-aware buttons
        buttons = []
        if status != "Done":
            buttons.append(InlineKeyboardButton("✅ Done", callback_data=f"goal:done:{i}"))
        if status == "Active":
            buttons.append(InlineKeyboardButton("⏸ Pause", callback_data=f"goal:pause:{i}"))
        if status == "Paused":
            buttons.append(InlineKeyboardButton("▶️ Resume", callback_data=f"goal:resume:{i}"))
        buttons.append(InlineKeyboardButton("🗑️ Delete", callback_data=f"goal:delete:{i}"))

        keyboard = InlineKeyboardMarkup([buttons])
        await update.message.reply_text(label, reply_markup=keyboard)


async def goals_categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show goals organized by category with headers."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.is_empty():
        await update.message.reply_text("No goals yet. Use /goals to add some.")
        return

    content = store.read()
    # Extract each section and format it cleanly
    sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)
    output_parts = []

    for section in sections:
        if not section.strip() or section.startswith("# Goals"):
            continue
        header_match = re.match(r"## (.+)", section)
        if not header_match:
            continue
        header = header_match.group(1).strip()
        rows = []
        for line in section.splitlines():
            line = line.strip()
            if line.startswith("|") and "|" in line[1:]:
                if all(c in "-| " for c in line):
                    continue
                parts = [p.strip() for p in line.strip("|").split("|")]
                if len(parts) < 3:
                    continue
                if parts[0] in ("Status", "Difficulty", "Frequency", "Goal", "Habit"):
                    continue
                # Columns: Status | Difficulty/Frequency | Goal title
                status = parts[0]
                difficulty = parts[1]
                title = parts[2]
                rows.append(f"  [{status} · {difficulty}] {title}")
            elif line.startswith("- ") and "Open Backlog" in section:
                rows.append(f"  {line}")

        if rows:
            output_parts.append(f"📌 {header}\n" + "\n".join(rows))

    if not output_parts:
        await update.message.reply_text(
            "Goals file doesn't have categories yet.\n"
            "Use /goals to re-enter your goals and they'll be categorized automatically."
        )
        return

    await update.message.reply_text("\n\n".join(output_parts)[:4000])


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


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark a task as complete: /done <task description>"""
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Usage: /done <task description>\n"
            "Example: /done Research Agentic AI concepts"
        )
        return
    settings = get_settings()
    log = TasksLog(settings.tasks_log_path)
    await log.append(text, status="DONE")
    await update.message.reply_text(f"✅ Marked as done:\n{text}")


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log a progress update: /update <message>"""
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Usage: /update <progress note>\n"
            "Example: /update Finished research, starting the write-up"
        )
        return
    settings = get_settings()
    log = TasksLog(settings.tasks_log_path)
    await log.append(text, status="UPDATE")
    await update.message.reply_text(f"📝 Update logged:\n{text}")


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


async def focus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /focus          — show current focus goals
    /focus <text>   — set weekly focus goals
    """
    if not _is_allowed(update):
        return
    settings = get_settings()
    focus_store = FocusStore(settings.autonomous_md_path.parent / "focus.md")
    text = " ".join(context.args) if context.args else ""

    if text:
        focus_store.set(text)
        await update.message.reply_text(
            f"🎯 Focus set:\n{text}\n\n"
            "Tomorrow's task generation will prioritize these goals.\n"
            "Use /unfocus to clear."
        )
    else:
        current = focus_store.read()
        if current:
            await update.message.reply_text(f"🎯 Current focus:\n{current}")
        else:
            await update.message.reply_text(
                "No focus set. Use /focus <goals> to prioritize.\n"
                "Example: /focus Launch SaaS MVP, Daily exercise habit"
            )


async def unfocus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    settings = get_settings()
    FocusStore(settings.autonomous_md_path.parent / "focus.md").clear()
    await update.message.reply_text("Focus cleared. All goals weighted equally.")


async def goal_done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark a goal as complete and move it to the Completed section."""
    if not _is_allowed(update):
        return
    title = " ".join(context.args) if context.args else ""
    if not title:
        await update.message.reply_text(
            "Usage: /goal_done <goal title>\n"
            "Example: /goal_done Launch SaaS MVP"
        )
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.mark_goal_done(title):
        await update.message.reply_text(
            f"🏆 Goal marked as done and archived:\n{title}"
        )
    else:
        await update.message.reply_text(
            f"Goal not found: '{title}'\n"
            "Use /goals_list to see exact goal titles."
        )


async def goal_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Update a goal's status: /goal_status <title> <status>
    Status: Active | In Progress | Paused | Done
    """
    if not _is_allowed(update):
        return
    args = context.args or []
    valid_statuses = {"Active", "In Progress", "Paused", "Done"}

    # Last arg is status, everything before is the title
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /goal_status <goal title> <status>\n"
            "Status options: Active | In Progress | Paused | Done\n"
            "Example: /goal_status Launch SaaS MVP In Progress"
        )
        return

    # Try matching status from the end
    status = None
    title_parts = list(args)
    for n in (2, 1):
        candidate = " ".join(args[-n:])
        if candidate in valid_statuses:
            status = candidate
            title_parts = args[:-n]
            break

    if not status or not title_parts:
        await update.message.reply_text(
            f"Valid statuses: {', '.join(valid_statuses)}\n"
            "Example: /goal_status Launch SaaS MVP In Progress"
        )
        return

    title = " ".join(title_parts)
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.update_goal_status(title, status):
        await update.message.reply_text(f"Updated '{title}' → {status}")
    else:
        await update.message.reply_text(
            f"Goal not found: '{title}'\nUse /goals_list to see exact titles."
        )


async def goal_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a goal entirely: /goal_delete <title>"""
    if not _is_allowed(update):
        return
    title = " ".join(context.args) if context.args else ""
    if not title:
        await update.message.reply_text(
            "Usage: /goal_delete <goal title>\n"
            "Example: /goal_delete Learn Spanish"
        )
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.delete_goal(title):
        await update.message.reply_text(f"🗑️ Goal deleted:\n{title}")
    else:
        await update.message.reply_text(
            f"Goal not found: '{title}'\n"
            "Use /goals_list to see exact goal titles."
        )


async def goal_edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Rename a goal: /goal_edit <old title> | <new title>
    The pipe character separates old and new title.
    """
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if "|" not in text:
        await update.message.reply_text(
            "Usage: /goal_edit <old title> | <new title>\n"
            "Example: /goal_edit Learn Spanish | Learn Portuguese"
        )
        return
    parts = text.split("|", 1)
    old_title = parts[0].strip()
    new_title = parts[1].strip()
    if not old_title or not new_title:
        await update.message.reply_text("Both old and new titles must be non-empty.")
        return
    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    if store.edit_goal_title(old_title, new_title):
        await update.message.reply_text(f"✏️ Goal renamed:\n{old_title} → {new_title}")
    else:
        await update.message.reply_text(
            f"Goal not found: '{old_title}'\n"
            "Use /goals_list to see exact goal titles."
        )


async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Move a backlog item into active goals with LLM classification.
    /promote <item text>
    """
    if not _is_allowed(update):
        return
    item = " ".join(context.args) if context.args else ""
    if not item:
        await update.message.reply_text(
            "Usage: /promote <backlog item>\n"
            "Example: /promote Learn Spanish"
        )
        return

    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    goals = store.read()

    await update.message.reply_text(f"Classifying '{item}'...")

    try:
        from jacbotcoach.llm.router import LLMRouter
        router = LLMRouter()
        raw = await router.classify_backlog_item(item, goals)
        data = json.loads(raw)
        category = data.get("category", "Short Term Projects")
        difficulty = data.get("difficulty", "Medium")
        rephrased = data.get("rephrased", item)
        notes = data.get("notes", "")
    except Exception as e:
        logger.warning("LLM classification failed (%s), using defaults", e)
        category, difficulty, rephrased, notes = "Short Term Projects", "Medium", item, ""

    if store.promote_backlog_item(item, category, difficulty, rephrased, notes):
        await update.message.reply_text(
            f"✅ Promoted to {category}:\n"
            f"[{difficulty}] {rephrased}"
            + (f"\n{notes}" if notes else "")
        )
    else:
        await update.message.reply_text(
            f"Item not found in backlog: '{item}'\n"
            "Use /status to check the exact text in Open Backlog."
        )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/history <goal> — show task log entries related to a goal."""
    if not _is_allowed(update):
        return
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /history <goal title>\nExample: /history Learn Spanish")
        return
    settings = get_settings()
    log = TasksLog(settings.tasks_log_path)
    keywords = [w for w in query.lower().split() if len(w) > 3]
    matches = [
        line.strip() for line in log.read_all().splitlines()
        if line.strip().startswith("-") and any(kw in line.lower() for kw in keywords)
    ]
    if not matches:
        await update.message.reply_text(
            f"No task log entries found related to '{query}'.\n"
            "Tasks are matched by keywords in the goal title."
        )
        return
    await update.message.reply_text(
        f"Task history for '{query}' ({len(matches)} entries):\n\n" +
        "\n".join(matches[-20:])  # last 20 matching entries
    )


async def streaks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/streaks — view current habit streaks."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = StreakStore(settings.streaks_path)
    all_streaks = store.get_all()
    if not all_streaks:
        await update.message.reply_text(
            "No streaks tracked yet.\n"
            "Use /streak_done <habit> to log a habit and start a streak."
        )
        return
    lines = ["Habit streaks:\n"]
    for habit, data in all_streaks.items():
        active = data["active"]
        current = data["current"]
        best = data["best"]
        icon = "🔥" if active and current >= 3 else ("✅" if active else "💤")
        lines.append(f"{icon} {habit}")
        lines.append(f"   Current: {current} day{'s' if current != 1 else ''} | Best: {best}")
    await update.message.reply_text("\n".join(lines))


async def streak_done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/streak_done <habit> — log a habit completion and update streak."""
    if not _is_allowed(update):
        return
    habit = " ".join(context.args) if context.args else ""
    if not habit:
        await update.message.reply_text(
            "Usage: /streak_done <habit name>\nExample: /streak_done Daily Exercise"
        )
        return
    settings = get_settings()
    store = StreakStore(settings.streaks_path)
    result = store.record(habit)
    current = result["current"]
    lines = [f"🔥 {habit} — Day {current}!"]
    if result["new_best"]:
        lines.append(f"New personal best: {current} days!")
    if result["motivation"]:
        lines.append(f"\n{result['motivation']}")
    await update.message.reply_text("\n".join(lines))


async def milestone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/milestone <goal> | <step> — add a milestone to a goal."""
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if "|" not in text:
        await update.message.reply_text(
            "Usage: /milestone <goal> | <milestone step>\n"
            "Example: /milestone Publish Book | Write chapter outline"
        )
        return
    parts = text.split("|", 1)
    goal = parts[0].strip()
    step = parts[1].strip()
    if not goal or not step:
        await update.message.reply_text("Both goal and milestone must be non-empty.")
        return
    settings = get_settings()
    store = MilestoneStore(settings.milestones_path)
    store.add(goal, step)
    await update.message.reply_text(f"⬜ Milestone added to '{goal}':\n{step}")


async def milestone_done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/milestone_done <goal> | <step> — mark a milestone complete."""
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if "|" not in text:
        await update.message.reply_text(
            "Usage: /milestone_done <goal> | <milestone step>\n"
            "Example: /milestone_done Publish Book | Write chapter outline"
        )
        return
    parts = text.split("|", 1)
    goal = parts[0].strip()
    step = parts[1].strip()
    settings = get_settings()
    store = MilestoneStore(settings.milestones_path)
    if store.complete(goal, step):
        pct = store.progress_pct(goal)
        msg = f"✅ Milestone complete:\n{step}"
        if pct is not None:
            msg += f"\n\nProgress on '{goal}': {pct}%"
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text(f"Milestone not found for goal '{goal}'.")


async def milestones_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/milestones <goal> — show all milestones for a goal."""
    if not _is_allowed(update):
        return
    goal = " ".join(context.args) if context.args else ""
    if not goal:
        await update.message.reply_text(
            "Usage: /milestones <goal title>\nExample: /milestones Publish Book"
        )
        return
    settings = get_settings()
    store = MilestoneStore(settings.milestones_path)
    summary = store.summary(goal)
    pct = store.progress_pct(goal)
    header = f"Milestones for '{goal}'"
    if pct is not None:
        header += f" ({pct}% complete)"
    await update.message.reply_text(f"{header}\n\n{summary}")


async def coach_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/coach_notes — show the last 5 coaching session summaries."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = CoachNotesStore(settings.coach_notes_path)
    if store.is_empty():
        await update.message.reply_text(
            "No coaching notes saved yet.\n"
            "Start a session with /coach — notes are saved automatically when you /endcoach."
        )
        return
    notes = store.read_recent(n=5)
    await update.message.reply_text(f"📓 Recent coaching notes:\n\n{notes}"[:4000])


async def research_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/research <topic> — queue a topic for overnight OpenClaw research."""
    if not _is_allowed(update):
        return
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text(
            "Usage: /research <topic>\n"
            "Example: /research Agentic AI frameworks in 2025\n\n"
            "Topics are researched overnight by an OpenClaw agent.\n"
            "Results are saved to research/<date>-<topic>.md and listed in your morning briefing."
        )
        return
    settings = get_settings()
    from jacbotcoach.storage.research_queue import ResearchQueue
    queue = ResearchQueue(settings.research_queue_path)
    count, already_done = await queue.enqueue(topic)
    if already_done:
        await update.message.reply_text(
            f"🔬 '{topic}' was already researched.\n\n"
            "Use /research_retry to re-run it, or check /research_list for the saved report."
        )
        return
    await update.message.reply_text(
        f"🔬 Research queued: {topic}\n\n"
        f"Queue depth: {count} topic(s). Results will appear in your morning briefing."
    )


async def research_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/research_list — show the research queue and any ready reports."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    from jacbotcoach.storage.research_queue import ResearchQueue
    queue = ResearchQueue(settings.research_queue_path)
    all_items = queue.get_all()

    if not all_items:
        await update.message.reply_text(
            "No research queued. Use /research <topic> to queue a topic."
        )
        return

    pending = [i for i in all_items if i["status"] == "pending"]
    done = [i for i in all_items if i["status"] == "done"]
    # legacy: items marked "spawned" by old code
    spawned = [i for i in all_items if i["status"] == "spawned"]
    failed = [i for i in all_items if i["status"] == "failed"]

    lines = ["Research queue:\n"]

    if done:
        lines.append(f"✅ Completed ({len(done)}):")
        for item in done:
            lines.append(f"  • {item['topic']} (done {item.get('done_at', '?')})")
        lines.append("")

    if pending:
        lines.append(f"⏳ Pending ({len(pending)}):")
        for item in pending:
            lines.append(f"  • {item['topic']} (queued {item['queued_at']})")
        lines.append("")

    if spawned:
        lines.append(f"🔄 Stuck/in-progress ({len(spawned)}) — use /research_retry:")
        for item in spawned:
            lines.append(f"  • {item['topic']}")
        lines.append("")

    if failed:
        lines.append(f"❌ Failed ({len(failed)}):")
        for item in failed:
            lines.append(f"  • {item['topic']}: {item.get('error', 'unknown')[:80]}")

    await update.message.reply_text("\n".join(lines).strip())


async def idea_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/idea <text> — capture a quick idea with no LLM processing."""
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Usage: /idea <your idea>\n"
            "Example: /idea Build a habit tracker that syncs with Apple Health\n\n"
            "Ideas are saved instantly to memory/ideas.md.\n"
            "Use /ideas to review them or /promote_idea to move one to your backlog."
        )
        return
    settings = get_settings()
    from jacbotcoach.storage.ideas import IdeaStore
    store = IdeaStore(settings.ideas_path)
    count = store.append(text)
    await update.message.reply_text(
        f"💡 Idea captured!\n{text}\n\n"
        f"You have {count} idea{'s' if count != 1 else ''} saved."
    )


async def ideas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ideas — list all captured ideas."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    from jacbotcoach.storage.ideas import IdeaStore
    store = IdeaStore(settings.ideas_path)
    ideas = store.read_all()
    if not ideas:
        await update.message.reply_text(
            "No ideas saved yet. Use /idea <text> to capture one."
        )
        return
    lines = [f"💡 Your ideas ({len(ideas)} total):\n"]
    for i, idea in enumerate(ideas, 1):
        lines.append(f"{i}. {idea[2:]}")  # strip leading "- "
    await update.message.reply_text("\n".join(lines)[:4000])


async def promote_idea_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/promote_idea <text> — move a matching idea to the Open Backlog."""
    if not _is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Usage: /promote_idea <idea text or keyword>\n"
            "Example: /promote_idea habit tracker\n\n"
            "This moves the matching idea into your Open Backlog in AUTONOMOUS.md."
        )
        return
    settings = get_settings()
    from jacbotcoach.storage.ideas import IdeaStore
    idea_store = IdeaStore(settings.ideas_path)

    ideas = idea_store.read_all()
    needle = text.strip().lower()
    match = next((line for line in ideas if needle in line.lower()), None)

    if not match:
        await update.message.reply_text(
            f"No idea matching '{text}' found.\n"
            "Use /ideas to see the full list."
        )
        return

    plain_text = idea_store.extract_text(match)

    auto_store = AutonomousStore(settings.autonomous_md_path)
    if auto_store.is_empty():
        await update.message.reply_text(
            "No goals file found. Use /goals to set up your goals first."
        )
        return

    if auto_store.add_to_backlog(plain_text):
        idea_store.remove(text)
        await update.message.reply_text(
            f"✅ Moved to Open Backlog:\n{plain_text}\n\n"
            "Use /promote to classify it into an active goal."
        )
    else:
        await update.message.reply_text(
            f"Could not add to backlog. Use /status to check your goals file."
        )


async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /approve — adopt the pending weekly plan proposal as this week's focus.
    Sent by the bot Sunday evening; the user runs /approve to accept.
    """
    if not _is_allowed(update):
        return
    settings = get_settings()
    from jacbotcoach.storage.weekly_plan import WeeklyPlanStore
    plan_store = WeeklyPlanStore(settings.weekly_plan_path)
    proposal = plan_store.approve()
    if not proposal:
        await update.message.reply_text(
            "No pending weekly plan to approve.\n"
            "The bot sends a proposal Sunday evening around 6 PM,\n"
            "or use /focus <text> to set your focus manually."
        )
        return
    FocusStore(settings.autonomous_md_path.parent / "focus.md").set(proposal)
    await update.message.reply_text(
        f"✅ Weekly focus approved and set:\n\n{proposal}\n\n"
        "Task generation will prioritize these goals all week.\n"
        "Use /focus <text> to adjust, or /unfocus to clear."
    )


async def draft_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/draft <topic or chapter> — queue a first-draft writing session and run it now."""
    if not _is_allowed(update):
        return
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text(
            "Usage: /draft <topic or chapter>\n"
            "Examples:\n"
            "  /draft Chapter 3: The Case for Deep Work\n"
            "  /draft Introduction to my productivity book\n"
            "  /draft Blog post on async Python patterns\n\n"
            "Drafts are written overnight by an OpenClaw agent.\n"
            "Results are saved to drafts/<date>-<topic>.md and listed in your morning briefing."
        )
        return
    settings = get_settings()
    from jacbotcoach.storage.draft_queue import DraftQueue
    queue = DraftQueue(settings.draft_queue_path)
    count, already_done = await queue.enqueue(topic)
    if already_done:
        await update.message.reply_text(
            f"✍️ '{topic}' was already drafted.\n\n"
            "Use /draft_retry to re-run it, or check /drafts for the saved file."
        )
        return
    await update.message.reply_text(
        f"✍️ Draft queued: {topic}\n\n"
        f"Queue depth: {count} draft(s). "
        "Use /draft_trigger to start writing now, or it will run automatically at 1 AM."
    )


async def drafts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/drafts — show the draft queue and any completed drafts."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    from jacbotcoach.storage.draft_queue import DraftQueue
    queue = DraftQueue(settings.draft_queue_path)
    all_items = queue.get_all()

    if not all_items:
        await update.message.reply_text(
            "No drafts queued. Use /draft <topic> to queue one."
        )
        return

    pending = [i for i in all_items if i["status"] == "pending"]
    done = [i for i in all_items if i["status"] == "done"]
    # legacy: items marked "spawned" by old code
    spawned = [i for i in all_items if i["status"] == "spawned"]
    failed = [i for i in all_items if i["status"] == "failed"]

    lines = ["Draft queue:\n"]

    if done:
        lines.append(f"✅ Completed ({len(done)}):")
        for item in done:
            lines.append(f"  • {item['topic']} (done {item.get('done_at', '?')})")
        lines.append("")

    if pending:
        lines.append(f"⏳ Pending ({len(pending)}):")
        for item in pending:
            lines.append(f"  • {item['topic']} (queued {item['queued_at']})")
        lines.append("")

    if spawned:
        lines.append(f"🔄 Stuck/in-progress ({len(spawned)}) — use /draft_retry:")
        for item in spawned:
            lines.append(f"  • {item['topic']}")
        lines.append("")

    if failed:
        lines.append(f"❌ Failed ({len(failed)}):")
        for item in failed:
            lines.append(f"  • {item['topic']}: {item.get('error', 'unknown')[:80]}")

    await update.message.reply_text("\n".join(lines).strip())


async def research_trigger_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/research_trigger — run the research job now (don't wait for midnight)."""
    if not _is_allowed(update):
        return
    await update.message.reply_text("Starting research now...")
    try:
        from jacbotcoach.scheduler.jobs import run_research_job
        await run_research_job(context.application)
    except Exception as e:
        await update.message.reply_text(f"Error during research: {e}")


async def research_retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/research_retry [topic] — re-run a failed or already-completed research topic."""
    if not _is_allowed(update):
        return
    topic = " ".join(context.args) if context.args else ""
    settings = get_settings()
    from jacbotcoach.storage.research_queue import ResearchQueue
    queue = ResearchQueue(settings.research_queue_path)
    all_items = queue.get_all()

    if topic:
        match = next(
            (i for i in all_items if i["topic"].lower() == topic.lower() and i["status"] in ("failed", "done", "spawned")),
            None,
        )
        if not match:
            await update.message.reply_text(
                f"No completed or failed research found matching '{topic}'.\n"
                "Use /research_list to see the queue."
            )
            return
        await queue.reset_to_pending(match["topic"])
        await update.message.reply_text(f"Reset to pending: {match['topic']}\nStarting now...")
    else:
        retryable = [i for i in all_items if i["status"] in ("failed", "spawned")]
        if not retryable:
            await update.message.reply_text(
                "No failed or stuck research to retry.\n"
                "To re-run a completed topic use /research_retry <topic>."
            )
            return
        for item in retryable:
            await queue.reset_to_pending(item["topic"])
        await update.message.reply_text(
            f"Reset {len(retryable)} research topic(s) to pending. Starting now..."
        )
    try:
        from jacbotcoach.scheduler.jobs import run_research_job
        await run_research_job(context.application)
    except Exception as e:
        await update.message.reply_text(f"Error during research: {e}")


async def build_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/build <idea> — queue a project idea for overnight OpenClaw scaffolding."""
    if not _is_allowed(update):
        return
    idea = " ".join(context.args) if context.args else ""
    if not idea:
        await update.message.reply_text(
            "Usage: /build <idea>\n"
            "Examples:\n"
            "  /build CLI tool to batch rename files by regex\n"
            "  /build Telegram bot that tracks daily water intake\n"
            "  /build FastAPI service for personal link bookmarking\n\n"
            "Overnight, an OpenClaw agent scaffolds a working prototype in ~/projects/.\n"
            "Results are listed in your morning briefing."
        )
        return
    settings = get_settings()
    from jacbotcoach.storage.project_queue import ProjectQueue
    queue = ProjectQueue(settings.project_queue_path)
    count = await queue.enqueue(idea)
    await update.message.reply_text(
        f"🔨 Build queued: {idea}\n\n"
        f"Queue depth: {count} project(s). "
        "A prototype will be scaffolded overnight and listed in your morning briefing."
    )


async def builds_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/builds — show the project build queue and any completed prototypes."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    from jacbotcoach.storage.project_queue import ProjectQueue
    queue = ProjectQueue(settings.project_queue_path)
    all_items = queue.get_all()

    if not all_items:
        await update.message.reply_text(
            "No builds queued. Use /build <idea> to queue a project."
        )
        return

    pending = [i for i in all_items if i["status"] == "pending"]
    spawned = [i for i in all_items if i["status"] == "spawned"]
    failed = [i for i in all_items if i["status"] == "failed"]
    ready = [
        i for i in spawned
        if Path(i.get("output_path", "NONE")).exists()
    ]

    lines = ["Project build queue:\n"]

    if ready:
        lines.append(f"✅ Ready prototypes ({len(ready)}):")
        for item in ready:
            lines.append(f"  • {item['idea']}")
            lines.append(f"    {item['output_path']}")
        lines.append("")

    if pending:
        lines.append(f"⏳ Pending ({len(pending)}):")
        for item in pending:
            lines.append(f"  • {item['idea']} (queued {item['queued_at']})")
        lines.append("")

    in_progress = [i for i in spawned if i not in ready]
    if in_progress:
        lines.append(f"🔄 In progress ({len(in_progress)}):")
        for item in in_progress:
            lines.append(f"  • {item['idea']}")
        lines.append("")

    if failed:
        lines.append(f"❌ Failed ({len(failed)}):")
        for item in failed:
            lines.append(f"  • {item['idea']}: {item.get('error', 'unknown')[:80]}")

    await update.message.reply_text("\n".join(lines).strip())


async def score_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/score — show the most recent weekly accountability score with breakdown."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AccountabilityStore(settings.accountability_scores_path)
    entry = store.get_latest()
    if not entry:
        await update.message.reply_text(
            "No accountability scores yet.\n"
            "Scores are calculated automatically every Sunday at 7 PM.\n"
            "You'll get your first score this Sunday!"
        )
        return

    def _bar(score: int) -> str:
        filled = round(score / 2)
        return "█" * filled + "░" * (5 - filled)

    overall = entry["overall"]
    week_ending = entry["week_ending"]
    text = (
        f"📊 Accountability Score — week ending {week_ending}\n\n"
        f"Consistency     {_bar(entry['consistency'])} {entry['consistency']}/10\n"
        f"Focus Alignment {_bar(entry['focus_alignment'])} {entry['focus_alignment']}/10\n"
        f"Momentum        {_bar(entry['momentum'])} {entry['momentum']}/10\n\n"
        f"Overall: {overall}/10\n"
        f"Tasks: {entry['done_count']} done / {entry['scheduled_count']} scheduled\n\n"
        f"💬 {entry['insight'].strip()}"
    )
    await update.message.reply_text(text)


async def scores_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scores — show accountability score history (last 12 weeks)."""
    if not _is_allowed(update):
        return
    settings = get_settings()
    store = AccountabilityStore(settings.accountability_scores_path)
    history = store.get_history(weeks=12)
    if not history:
        await update.message.reply_text(
            "No score history yet. Scores are recorded every Sunday evening."
        )
        return

    lines = [f"📊 Accountability Score History ({len(history)} week{'s' if len(history) != 1 else ''})\n"]
    for entry in history:
        overall = entry["overall"]
        bar_len = round(overall / 2)
        bar = "█" * bar_len + "░" * (5 - bar_len)
        lines.append(
            f"{entry['week_ending']}  {bar} {overall}/10  "
            f"(C:{entry['consistency']} F:{entry['focus_alignment']} M:{entry['momentum']})"
        )

    if len(history) >= 2:
        avg = round(sum(e["overall"] for e in history) / len(history), 1)
        lines.append(f"\n{len(history)}-week average: {avg}/10")

    await update.message.reply_text("\n".join(lines))


async def draft_trigger_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/draft_trigger — run the content drafting job now (don't wait for 1 AM)."""
    if not _is_allowed(update):
        return
    await update.message.reply_text("Starting content drafting now...")
    try:
        from jacbotcoach.scheduler.jobs import run_content_drafting_job
        await run_content_drafting_job(context.application)
    except Exception as e:
        await update.message.reply_text(f"Error during content drafting: {e}")


async def draft_retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/draft_retry [topic] — re-run a failed or already-completed draft."""
    if not _is_allowed(update):
        return
    topic = " ".join(context.args) if context.args else ""
    settings = get_settings()
    from jacbotcoach.storage.draft_queue import DraftQueue
    queue = DraftQueue(settings.draft_queue_path)

    if topic:
        reset = await queue.reset_to_pending(topic)
        if not reset:
            await update.message.reply_text(
                f"No completed or failed draft found matching '{topic}'.\n"
                "Use /drafts to see the queue."
            )
            return
        await update.message.reply_text(f"Reset to pending: {topic}\nStarting now...")
    else:
        all_items = queue.get_all()
        retryable = [i for i in all_items if i["status"] in ("failed", "spawned")]
        if not retryable:
            await update.message.reply_text(
                "No failed or stuck drafts to retry.\n"
                "To re-run a completed draft use /draft_retry <topic>."
            )
            return
        for item in retryable:
            await queue.reset_to_pending(item["topic"])
        await update.message.reply_text(
            f"Reset {len(retryable)} failed/stuck draft(s) to pending. Starting now..."
        )

    try:
        from jacbotcoach.scheduler.jobs import run_content_drafting_job
        await run_content_drafting_job(context.application)
    except Exception as e:
        await update.message.reply_text(f"Error during content drafting: {e}")


async def nl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Natural language fallback — handles plain text messages not caught by
    other handlers. Classifies intent via LLM and routes to the right action.
    """
    if not _is_allowed(update):
        return
    message = update.message.text.strip()
    if not message:
        return

    settings = get_settings()
    store = AutonomousStore(settings.autonomous_md_path)
    goals = store.read() if not store.is_empty() else ""

    try:
        from jacbotcoach.llm.router import LLMRouter
        intent = await LLMRouter().detect_intent(message, goals)
    except Exception as e:
        logger.error("Intent detection failed: %s", e)
        await update.message.reply_text(
            "I didn't understand that. Use /start to see available commands."
        )
        return

    action = intent.get("action", "unknown")
    args = intent.get("args", "")
    logger.info("NL intent: action=%s args=%s message=%s", action, args, message)

    if action == "goal_done":
        if store.mark_goal_done(args):
            await update.message.reply_text(f"🏆 Goal marked complete: {args}")
        else:
            await update.message.reply_text(f"Goal not found: '{args}'. Use /goals_list to check titles.")

    elif action == "goal_status":
        if "|" in args:
            title, status = args.split("|", 1)
            store.update_goal_status(title.strip(), status.strip())
            await update.message.reply_text(f"Updated '{title.strip()}' → {status.strip()}")
        else:
            await update.message.reply_text("Couldn't parse that. Try: /goal_status <title> <status>")

    elif action == "goal_delete":
        if store.delete_goal(args):
            await update.message.reply_text(f"🗑️ Goal deleted: {args}")
        else:
            await update.message.reply_text(f"Goal not found: '{args}'.")

    elif action == "streaks":
        await streaks_command(update, context)

    elif action == "tasks":
        await tasks_command(update, context)

    elif action == "trigger":
        await trigger_command(update, context)

    elif action == "status":
        await status_command(update, context)

    elif action == "focus":
        if args:
            FocusStore(settings.autonomous_md_path.parent / "focus.md").set(args)
            await update.message.reply_text(f"🎯 Focus set: {args}")
        else:
            await focus_command(update, context)

    elif action == "coach":
        await update.message.reply_text("Starting coaching mode — use /coach to begin.")

    elif action == "unknown":
        await update.message.reply_text(
            "I'm not sure what you meant. Use /start to see all commands, "
            "or /coach to have a free-form conversation."
        )

    else:
        # For actions that map cleanly to a reply from the LLM
        reply = intent.get("reply", "")
        if reply:
            await update.message.reply_text(reply)
        else:
            await update.message.reply_text("Done. Use /start to see all commands.")
