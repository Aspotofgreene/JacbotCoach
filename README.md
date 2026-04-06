# JacbotCoach

A personal productivity bot that runs on your Mac mini and connects to your Telegram account. You send it your goals, and it autonomously generates daily tasks, dispatches them to AI agents for execution, and keeps you updated throughout the day.

---

## How It Works

JacbotCoach is made up of three layers working together:

1. **You (Telegram)** — You interact with the bot through Telegram commands or plain natural language. You set goals, check on tasks, log progress, and have coaching conversations.

2. **JacbotCoach (Mac mini)** — The bot process runs on your Mac mini. It reads your goals, talks to the LLMs, manages your goal file, and drives the daily schedule. It uses two Ollama instances for AI:
   - **Mac mini** (`qwen3:14b`) — fast, used for light tasks like coaching replies, goal classification, natural language parsing, and streak motivation
   - **Desktop GPU** (`qwen3:14b` or heavier model) — used for heavy tasks like generating daily tasks and writing detailed agent prompts. Falls back to the Mac mini if unreachable.

3. **OpenClaw agents** — When a task is ready to run, JacbotCoach spawns an OpenClaw session on the Mac mini. OpenClaw is an AI agent with access to your Mac's shell, file system, web browser, and developer tools. It executes the task autonomously and writes its results back to the task log. When it finishes, JacbotCoach notifies you on Telegram.

### Daily Schedule

| Time | Event |
|---|---|
| 6:00 AM | Morning briefing — goal status snapshot, yesterday's completions, today's task count, ready research reports and drafts |
| 8:00 AM | Daily task generation — spawns OpenClaw agents (count varies by day) |
| 10:00 AM | Check-in #1 — proactive deliverable progress check (if deliverables are set) |
| 1:00 PM | Check-in #2 — midday deliverable progress check |
| 4:00 PM | Check-in #3 — afternoon deliverable progress check |
| 8:00 PM | Evening reflection — completion summary + prompt to log manual wins |
| 9:00 PM | End-of-day wrap-up — deliverable summary, missed items analysis, coaching tip |
| Mon 7:00 AM | Stall detection — nudges goals with no activity in the past 7 days |
| Sun 9:00 AM | Weekly summary — accomplishments, stalled goals, recommendations |
| Sun 7:00 PM | Accountability scoring — 1-10 score across consistency, focus, and momentum |
| 12:00 AM | Research queue — spawns OpenClaw agents to research any queued topics |
| 1:00 AM | Content drafting — spawns OpenClaw agents to write first drafts for any queued topics |
| 2:00 AM | Project builder — spawns OpenClaw agents to scaffold prototypes for any queued build ideas |

### Smart Scheduling

Task count adjusts automatically by day of week:

| Day | Tasks |
|---|---|
| Monday | 5 |
| Tuesday – Thursday | 4 |
| Friday | 3 |
| Saturday – Sunday | 2 |

### File Storage

| File | Purpose |
|---|---|
| `AUTONOMOUS.md` | Your goals, habits, backlog, and completed archive. Hard capped at 50 lines. Only JacbotCoach writes here. |
| `memory/tasks-log.md` | Append-only log of every task. OpenClaw agents append here when they finish work. |
| `focus.md` | Your current weekly focus goals. Influences what tasks get generated each morning. |
| `memory/streaks.json` | Habit streak tracker — current streak, personal best, last logged date. |
| `memory/milestones.md` | Per-goal milestone checklists for tracking sub-steps. |
| `memory/research-queue.json` | Research topic queue — tracks pending, in-progress, and completed research. |
| `research/<date>-<topic>.md` | Markdown summaries produced by overnight research agents. |
| `memory/draft-queue.json` | Content draft queue — tracks pending, in-progress, and completed drafts. |
| `drafts/<date>-<topic>.md` | First-draft documents produced by overnight drafting agents. |
| `memory/project-queue.json` | Project build queue — tracks pending, in-progress, and completed build requests. |
| `projects/<date>-<slug>/` | Working prototypes scaffolded by overnight build agents. |
| `memory/accountability-scores.json` | Weekly accountability scores — consistency, focus alignment, momentum, and overall rating. |
| `memory/checkins.json` | Today's 3 deliverables — completion status and progress notes. Resets each day. |

---

## Setup

### Requirements

- Mac mini running macOS with [Ollama](https://ollama.com) installed
- Desktop machine with a GPU also running Ollama (optional but recommended)
- [OpenClaw](https://openclaw.ai) installed and running on the Mac mini
- Python 3.11 (via Homebrew: `brew install python@3.11`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID (find it via [@userinfobot](https://t.me/userinfobot))

### Install

```bash
git clone https://github.com/Aspotofgreene/JacbotCoach.git
cd JacbotCoach

/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -e .

cp .env.example .env
# Edit .env with your tokens and addresses
```

### Pull Ollama models

On the **Mac mini**:
```bash
ollama pull qwen3:14b
```

On the **desktop** (optional, heavier model):
```bash
ollama pull qwen3:14b-q8_0
```

### Run

```bash
source .venv/bin/activate
jacbotcoach
```

On startup the bot checks connectivity to both Ollama instances and OpenClaw. Then send `/start` to your bot on Telegram.

### Autostart with launchd (macOS)

Create `~/Library/LaunchAgents/com.jacbotcoach.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.jacbotcoach</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>sleep 15 &amp;&amp; exec /Users/myminihome/JacbotCoach/.venv/bin/jacbotcoach</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/myminihome/JacbotCoach</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/myminihome/JacbotCoach/logs/bot.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/myminihome/JacbotCoach/logs/bot.err</string>
</dict>
</plist>
```

The 15-second delay ensures the network is ready before the bot connects to Telegram.

```bash
launchctl load ~/Library/LaunchAgents/com.jacbotcoach.plist
```

### Updating

```bash
jacbot-update
```

Or manually:
```bash
cd ~/JacbotCoach && git pull && source .venv/bin/activate && pip install -e .
# Then restart: pkill -f jacbotcoach && launchctl start com.jacbotcoach
```

---

## Commands

> **Tip:** You can also type naturally — e.g. *"mark Learn Spanish as done"* or *"show me today's tasks"* — and the bot will figure out what you mean.

### Getting Started

| Command | Description |
|---|---|
| `/start` | Show the full command reference |

### Goals

| Command | Description |
|---|---|
| `/goals` | Brain dump your goals in plain language. Type `/save` when done, then `/confirm` to send to AI for structuring. |
| `/goals_list` | View all goals with inline action buttons (Done / Pause / Resume / Delete). |
| `/goals_cat` | View goals grouped by category. |
| `/goals_reparse` | Re-run AI structuring on the current goals file. Use this if goals were saved as raw text. |
| `/status` | View the raw `AUTONOMOUS.md` file with line count. |

### Managing Individual Goals

| Command | Description |
|---|---|
| `/goal_done <title>` | Mark a goal complete and archive it. |
| `/goal_status <title> <status>` | Update status: `Active`, `In Progress`, `Paused`, or `Done`. |
| `/goal_edit <old> \| <new>` | Rename a goal. |
| `/goal_delete <title>` | Permanently remove a goal. |
| `/promote <item>` | Move a backlog item into active goals. AI classifies category, difficulty, and rewrites the title. |
| `/history <title>` | Show all task log entries related to a goal (matched by keyword). |

### Milestones

Break goals down into trackable sub-steps.

| Command | Description |
|---|---|
| `/milestone <goal> \| <step>` | Add a milestone to a goal. Example: `/milestone Publish Book \| Write chapter outline` |
| `/milestone_done <goal> \| <step>` | Mark a milestone complete. Shows updated completion percentage. |
| `/milestones <goal>` | Show all milestones for a goal with completion status. |

### Streaks

Track daily habit completion and build streaks. The bot sends motivational messages at key milestones (Day 1, 3, 7, 14, 21, 30, 60, 90).

| Command | Description |
|---|---|
| `/streaks` | View all current habit streaks with current and personal best counts. |
| `/streak_done <habit>` | Log a habit completion for today and update the streak. |

Streaks are also updated automatically when OpenClaw completes a task related to a habit.

### Focus

| Command | Description |
|---|---|
| `/focus` | Show current weekly focus goals. |
| `/focus <goals>` | Set weekly focus. Morning task generation will prioritize these. |
| `/unfocus` | Clear focus so all goals are weighted equally. |

### Tasks

| Command | Description |
|---|---|
| `/tasks` | Show all tasks logged today. |
| `/trigger` | Manually run task generation now without waiting for 8 AM. |
| `/done <description>` | Manually log something you completed yourself. |
| `/update <message>` | Log a progress note without marking anything complete. |

### Coaching

| Command | Description |
|---|---|
| `/coach` | Start a multi-turn coaching conversation. The AI reads your goals and responds as a direct, practical coach. |
| `/endcoach` | End the coaching session. |

During a coaching session, any plain text message goes to the coach. History is kept for up to 10 exchanges.

### Research Queue

Queue topics for overnight autonomous research. OpenClaw agents research each topic and save structured markdown summaries. Results appear in the next morning's briefing.

| Command | Description |
|---|---|
| `/research <topic>` | Queue a topic for overnight research. Example: `/research Agentic AI frameworks in 2025` |
| `/research_list` | Show the research queue — pending, in-progress, and ready reports with file paths. |

Research summaries are saved to `research/<date>-<topic>.md` and cover: overview, current developments, key tools/players, practical insights, and further reading.

### Content Drafting

Queue topics or chapters for overnight first-draft writing. OpenClaw agents write structured 800–2000 word drafts based on your goals. Results appear in the next morning's briefing.

| Command | Description |
|---|---|
| `/draft <topic>` | Queue a topic for overnight drafting. Examples: `/draft Chapter 3: Deep Work` or `/draft Blog post on async Python` |
| `/drafts` | Show the draft queue — pending, in-progress, and ready drafts with file paths. |

Drafts are saved to `drafts/<date>-<topic>.md` with a title heading, introduction, 3–5 body sections, and conclusion. Tone and depth are shaped by your goals context.

### Project Builder

Queue ideas for overnight prototype scaffolding. OpenClaw agents scaffold a working prototype with a README, runnable entry point, and dependencies. Results appear in the next morning's briefing.

| Command | Description |
|---|---|
| `/build <idea>` | Queue a project idea for overnight scaffolding. Example: `/build CLI tool to batch rename files by regex` |
| `/builds` | Show the build queue — pending, in-progress, and ready prototypes with directory paths. |

Prototypes are saved to `projects/<date>-<slug>/` and include a README, at least one working feature demonstrating the core concept, and all required dependencies listed.

### Daily Check-Ins

Each morning, send your top 3 specific, time-bound deliverables as a numbered list and the bot automatically saves them. No command needed — just type them.

```
1. Finish Chapter 1 draft by 11 AM
2. Email clients by 2 PM
3. Code review by 5 PM
```

The bot then checks in at 10 AM, 1 PM, and 4 PM with your current status, and wraps up at 9 PM with a coached summary of what was done and what slipped.

| Command | Description |
|---|---|
| `/deliverables` | Show today's 3 deliverables and completion status. |
| `/done_d <n>` | Mark deliverable n (1, 2, or 3) as complete. |
| `/obstacle <n> <text>` | Report an obstacle on deliverable n. The bot responds immediately with 2-3 concrete unblocking suggestions. |

**Tip:** You can also say things like *"done on deliverable 2"* or *"I'm stuck on number 1 because..."* in plain text — the bot will route it automatically.

Obstacle advice runs immediately on the Mac mini LLM, so you get a response within seconds, not hours.

---

### Accountability Scoring

Every Sunday at 7 PM, the bot automatically calculates a weekly 1-10 score across three dimensions and sends it to Telegram. Scores are tracked in `memory/accountability-scores.json` over time.

| Dimension | How it's measured |
|---|---|
| **Consistency** | Tasks completed ÷ tasks scheduled this week |
| **Focus Alignment** | Completed tasks that mention your weekly focus keywords |
| **Momentum** | This week's completions vs. last week's (week-over-week trend) |

Each score comes with a 2-3 sentence AI coaching insight naming the weakest dimension and giving a concrete suggestion.

| Command | Description |
|---|---|
| `/score` | Show the most recent weekly score with breakdown and coaching insight. |
| `/scores` | Show score history across the last 12 weeks with overall average. |

---

## Troubleshooting

**Bot is running but not responding to messages**
- Check that `TELEGRAM_ALLOWED_USER_ID` in `.env` matches your actual Telegram ID (get it from [@userinfobot](https://t.me/userinfobot))
- Make sure only one instance is running: `pgrep -fl jacbotcoach` should show exactly one PID
- If multiple PIDs: `pkill -f jacbotcoach && sleep 2 && launchctl start com.jacbotcoach`

**Goals saved as raw text instead of structured tables**
- Run `/goals_reparse` to re-send through AI structuring

**LLM calls failing (404)**
- Ollama isn't running, or the model name in `.env` doesn't match an installed model
- Check: `ollama list` — verify model names match `OLLAMA_MAC_MODEL` and `OLLAMA_DESKTOP_MODEL`
- Start Ollama if not running: open the Ollama app or run `ollama serve`

**Bot fails to start on boot**
- Usually the bot starts before the network is ready — add a `sleep 15` delay in the launchd plist (see Autostart section)

---

## Configuration

All settings are in `.env`. Copy `.env.example` to get started.

| Setting | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Your BotFather token |
| `TELEGRAM_ALLOWED_USER_ID` | — | Your Telegram user ID (only you can use the bot) |
| `OLLAMA_MAC_URL` | `http://localhost:11434` | Ollama on the Mac mini |
| `OLLAMA_MAC_MODEL` | `qwen3:14b` | Model for light tasks |
| `OLLAMA_DESKTOP_URL` | `http://192.168.50.206:11434` | Ollama on the desktop GPU |
| `OLLAMA_DESKTOP_MODEL` | `qwen3:14b` | Model for heavy tasks |
| `DAILY_TASK_HOUR` | `8` | Hour to run daily task generation (24h) |
| `DAILY_TASK_MINUTE` | `0` | Minute offset |
| `MORNING_BRIEF_HOUR` | `6` | Hour to send morning briefing |
| `EVENING_REFLECTION_HOUR` | `20` | Hour to send evening reflection prompt |
| `STALL_DAYS` | `7` | Days of inactivity before a goal gets a nudge |
| `TIMEZONE` | `America/New_York` | Scheduler timezone |
| `AUTONOMOUS_MD_PATH` | `AUTONOMOUS.md` | Path to goals file |
| `TASKS_LOG_PATH` | `memory/tasks-log.md` | Path to task log |
| `STREAKS_PATH` | `memory/streaks.json` | Path to streak data |
| `MILESTONES_PATH` | `memory/milestones.md` | Path to milestone data |
| `RESEARCH_QUEUE_PATH` | `memory/research-queue.json` | Path to research queue |
| `RESEARCH_DIR` | `research` | Directory where research summaries are saved |
| `DRAFT_QUEUE_PATH` | `memory/draft-queue.json` | Path to draft queue |
| `DRAFTS_DIR` | `drafts` | Directory where draft documents are saved |
| `PROJECT_QUEUE_PATH` | `memory/project-queue.json` | Path to project build queue |
| `PROJECTS_DIR` | `projects` | Directory where scaffolded prototypes are saved |
| `PROJECT_BUILDER_HOUR` | `2` | Hour to run overnight project builder (24h) |
| `ACCOUNTABILITY_SCORES_PATH` | `memory/accountability-scores.json` | Path to weekly accountability scores |
| `ACCOUNTABILITY_SCORING_HOUR` | `19` | Hour to run Sunday accountability scoring (24h) |
| `CHECKINS_PATH` | `memory/checkins.json` | Path to today's deliverables data |
| `CHECKIN_MORNING_HOUR` | `10` | Hour for the first daily check-in (24h) |
| `CHECKIN_MIDDAY_HOUR` | `13` | Hour for the midday check-in (24h) |
| `CHECKIN_AFTERNOON_HOUR` | `16` | Hour for the afternoon check-in (24h) |
| `CHECKIN_EVENING_HOUR` | `21` | Hour for the end-of-day wrap-up (24h) |
