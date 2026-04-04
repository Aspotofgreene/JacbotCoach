# JacbotCoach — Developer Context

## What This Is
A personal Telegram bot that runs on a Mac mini. It reads the user's goals, generates daily tasks via LLM, spawns OpenClaw AI agents to execute those tasks overnight, and tracks progress. Stack: Python 3.11, python-telegram-bot v21+, APScheduler, Ollama, OpenClaw CLI.

## Hardware
- **Mac mini** (192.168.50.6, 24GB RAM) — runs the bot, Ollama (qwen3:14b), OpenClaw
- **Desktop GPU** (192.168.50.206, RTX 5080 16GB) — runs Ollama for heavy tasks (qwen3:14b or heavier)

## Critical Rules
- `app.run_polling()` manages its own event loop — NEVER wrap in `asyncio.run()`
- Scheduler is started in `_post_init` callback (same loop as run_polling)
- `AUTONOMOUS.md` is hard-capped at 50 lines — only the bot writes here, never OpenClaw
- `memory/tasks-log.md` is append-only — OpenClaw appends DONE/UPDATE lines here
- Always update `README.md` on GitHub after code changes (use mcp__github__create_or_update_file)
- Development branch: `claude/build-jacbotcoach-bot-yutps`

## File Map
```
src/jacbotcoach/
  main.py                  — app setup, handler registration, post_init/post_shutdown
  config.py                — pydantic-settings Settings (reads .env)
  watcher.py               — polls tasks-log.md every 30s, sends Telegram on DONE/UPDATE,
                             auto-links tasks to goals, auto-updates habit streaks
  bot/
    handlers.py            — all command handlers + nl_handler (NL intent routing)
    callbacks.py           — inline keyboard callback handlers (goal:done/pause/resume/delete)
    conversations.py       — /goals ConversationHandler (/save → /confirm flow)
    coach.py               — /coach /endcoach ConversationHandler
  llm/
    client.py              — OllamaClient (httpx, /api/generate)
    router.py              — LLMRouter: routes light→Mac mini, heavy→Desktop GPU
  scheduler/
    jobs.py                — all scheduled jobs (morning brief, daily tasks, evening reflection,
                             stall detection, weekly summary)
  storage/
    autonomous.py          — AutonomousStore: read/write AUTONOMOUS.md, goal CRUD methods
    tasks_log.py           — TasksLog: append-only, asyncio.Lock
    focus.py               — FocusStore: simple text file for weekly focus
    streaks.py             — StreakStore: JSON habit streak tracker (memory/streaks.json)
    milestones.py          — MilestoneStore: per-goal milestone checklists (memory/milestones.md)
  openclaw/
    client.py              — OpenClawClient: spawns `openclaw agent --agent main` via subprocess
```

## LLM Routing
| Task | Machine | Method |
|---|---|---|
| Goal parsing, coaching, intent detection, stall nudges | Mac mini | `Complexity.LIGHT` |
| Daily task generation, session prompt writing, weekly summary | Desktop GPU | `Complexity.HEAVY` (with Mac mini fallback) |

## Scheduled Jobs (all times local timezone)
| Time | Job |
|---|---|
| 6:00 AM daily | `run_morning_brief_job` — goal snapshot, yesterday's completions |
| 8:00 AM daily | `run_daily_job` — generate tasks, spawn OpenClaw agents |
| 8:00 PM daily | `run_evening_reflection_job` — completion count + log prompt |
| Monday 7:00 AM | `run_stall_detection_job` — nudge goals inactive >7 days |
| Sunday 9:00 AM | `run_weekly_summary_job` — weekly digest |

Smart scheduling: Mon=5 tasks, Tue-Thu=4, Fri=3, Sat-Sun=2.

## Implemented Commands
Goals: /goals (save/confirm flow), /goals_list (inline buttons), /goals_cat, /goals_reparse,
       /goal_done, /goal_status, /goal_edit, /goal_delete, /promote, /history, /status
Milestones: /milestone, /milestone_done, /milestones
Streaks: /streaks, /streak_done
Focus: /focus, /unfocus
Tasks: /tasks, /trigger, /done, /update
Coaching: /coach, /endcoach
NL fallback: plain text messages → detect_intent() → route to right action

## Key Patterns

### Adding a new command
1. Write the handler in `bot/handlers.py`
2. Import and register in `main.py` with `app.add_handler(CommandHandler("cmd", handler))`
3. Add to the `/start` help text in `handlers.py`
4. Update README.md on GitHub

### Adding a scheduled job
1. Write `async def run_X_job(application: Application)` in `scheduler/jobs.py`
2. Add to `build_scheduler()` with a CronTrigger
3. Import scheduler settings from `config.py` if timing is configurable

### Adding a new storage file
1. Create `storage/X.py` with a class that reads/writes a file in `memory/`
2. Add the path to `config.py` as `x_path: Path = Path("memory/x.json")`
3. Instantiate as `XStore(settings.x_path)` in handlers/jobs

### LLM router — adding a new method
- Light (Mac mini): `client = self._client(Complexity.LIGHT)`
- Heavy (Desktop): `client = await self._client_with_fallback(Complexity.HEAVY)`
- Always set a timeout: `await client.generate(prompt, timeout=60.0)`
- For JSON output: use `re.search(r"\{.*\}", raw, re.DOTALL)` to extract reliably

### OpenClaw session spawning
```python
result = await claw.create_session(prompt=session_prompt, label=task[:80])
# result.session_id — use for tracking
```
The session prompt must end with:
"When done, append a ✅ line to memory/tasks-log.md. Never edit AUTONOMOUS.md directly."

## AUTONOMOUS.md Format
```markdown
# Goals

## Projects — Short Term (1–2 months)
| Status | Difficulty | Goal | Notes |
|---|---|---|---|
| Active | Medium | Goal title | Notes |

## Projects — Long Term (3+ months)
| Status | Difficulty | Goal | Notes |
|---|---|---|---|

## Habits & Ongoing
| Status | Frequency | Habit | Why |
|---|---|---|---|

## Open Backlog
- Raw idea

## Completed
(empty)
```
Column order: Status | Difficulty/Frequency | Title | Notes  
Status values: Active | In Progress | Paused | Done

## tasks-log.md Format
```
- [2026-04-04] [DONE] ✅ Task description
- [2026-04-04] [SCHEDULED] 🔄 Task description
- [2026-04-04] [UPDATE] 📝 Progress note
- [2026-04-04] [SPAWN_FAILED] 🔄 Task description
```

## Planned Features (not yet built)
- **Research Queue** — /research <topic> queues topics; midnight job spawns OpenClaw to research and save markdown summaries to research/<date>-<topic>.md; morning briefing lists ready reports
- **Overnight Project Builder** — /build <idea> queues a build request; overnight OpenClaw scaffolds a working prototype in ~/projects/
- **Content Drafter** — /draft <topic> queues overnight first-draft writing (supports book goal); saves to drafts/<date>-<topic>.md
- **Quick Idea Capture** — /idea <text> appends to ideas.md with no LLM processing; /ideas lists them; /promote_idea moves to backlog
- **Weekly Planning Session** — Sunday evening bot proposes next week's focus based on what worked; user approves or adjusts with /approve or /focus
- **Decision Journal** — /decide <question> gives a recommendation grounded in user's actual goals
- **Accountability Scoring** — weekly 1-10 rating across consistency, focus alignment, momentum; tracked over time in memory/scores.json
- **Voice Message Support** — Telegram voice notes transcribed via Whisper (runs on desktop GPU); transcription treated as a brain dump or coaching message, great for capturing ideas on the go

## Git Workflow
- Branch: `claude/build-jacbotcoach-bot-yutps`
- Push: `git push -u origin claude/build-jacbotcoach-bot-yutps`
- If push fails (remote has new commits): `git pull --rebase origin claude/build-jacbotcoach-bot-yutps` then push again
- Always update GitHub README after code changes

## Mac Mini Update Command
User runs `jacbot-update` on the Mac mini to pull latest and restart the bot.
