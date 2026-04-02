# JacbotCoach

JacbotCoach is a personal productivity bot that runs on your Mac mini and connects to your Telegram account. You send it your goals, and it autonomously generates daily tasks, dispatches them to AI agents for execution, and keeps you updated throughout the day.

---

## How It Works

### Architecture

JacbotCoach is made up of three layers working together:

1. **You (Telegram)** — You interact with the bot entirely through Telegram commands. You set goals, check on tasks, log progress, and have coaching conversations.

2. **JacbotCoach (Mac mini)** — The bot process runs on your Mac mini. It reads your goals, talks to the LLMs, manages your goal file, and drives the daily schedule. It uses two Ollama instances for AI:
   - **Mac mini** (`llama3:14b`) — fast, used for light tasks like coaching replies, goal classification, and parsing
   - **Desktop GPU** (`deepseek-r1:14b`) — used for heavy tasks like generating daily tasks and writing detailed agent prompts. Falls back to the Mac mini if unreachable.

3. **OpenClaw agents** — When a task is ready to run, JacbotCoach spawns an OpenClaw session on the Mac mini. OpenClaw is an AI agent that has access to your Mac's shell, file system, web browser, and developer tools. It executes the task autonomously and writes its results back to the task log. When it finishes, JacbotCoach notifies you on Telegram.

### Daily Routine

Every morning at 8 AM (configurable), JacbotCoach:

1. Reads your goals from `AUTONOMOUS.md`
2. Reads your weekly focus (if set) and prepends it so the LLM weights tasks toward your priorities
3. Sends your goals to the desktop GPU to generate 4–5 concrete, executable tasks for the day
4. For each task, generates a detailed self-contained prompt for an OpenClaw agent
5. Spawns one OpenClaw session per task
6. Logs each task to `memory/tasks-log.md` with a `SCHEDULED` status
7. Sends you a Telegram summary of what was queued

Throughout the day, a background watcher polls the task log every 30 seconds. When OpenClaw finishes a task and writes a `DONE` or `UPDATE` line, you receive a Telegram notification automatically.

Every Sunday at 9 AM, a weekly summary is generated reviewing what was accomplished, which goals made progress, and what to focus on next.

### File Storage

| File | Purpose |
|---|---|
| `AUTONOMOUS.md` | Your goals, habits, backlog, and completed archive. Hard capped at 50 lines to stay token-efficient. Only JacbotCoach writes here — OpenClaw agents never touch it. |
| `memory/tasks-log.md` | Append-only log of every task — scheduled, completed, failed, or updated. OpenClaw agents append to this file when they finish work. |
| `focus.md` | Your current weekly focus goals. Influences what tasks get generated each morning. Cleared with `/unfocus`. |

---

## All Commands

### Getting Started

| Command | What it does |
|---|---|
| `/start` | Show the full command reference |

---

### Goals

Goals are stored in `AUTONOMOUS.md` in a structured markdown table organized by category. The LLM classifies and formats them automatically.

| Command | What it does |
|---|---|
| `/goals` | Start a brain dump. Send your goals in plain language — the LLM will parse, categorize, and structure them into your goals file. You can write in any format; it will sort out what is short-term, long-term, a habit, or a backlog idea. |
| `/goals_list` | View all goals as a flat numbered list regardless of category. |
| `/goals_cat` | View goals grouped by category (Short Term Projects, Long Term Projects, Habits & Ongoing, Open Backlog, Completed). |
| `/status` | View the raw `AUTONOMOUS.md` file with a line count. Useful for checking the underlying structure. |

---

### Managing Individual Goals

| Command | What it does |
|---|---|
| `/goal_done <title>` | Mark a goal as complete and move it to the Completed section. Uses partial, case-insensitive title matching. Example: `/goal_done Launch SaaS MVP` |
| `/goal_status <title> <status>` | Update a goal's status without completing it. Valid statuses: `Active`, `In Progress`, `Paused`, `Done`. The status goes at the end. Example: `/goal_status Launch SaaS MVP In Progress` |
| `/goal_edit <old title> \| <new title>` | Rename a goal. Separate old and new titles with a pipe character. Example: `/goal_edit Learn Spanish \| Learn Portuguese` |
| `/goal_delete <title>` | Permanently remove a goal from the file. Example: `/goal_delete Learn Spanish` |
| `/promote <item>` | Move an item from the Open Backlog into active goals. The LLM classifies it into the right category, assigns a difficulty, rephrases it into an action-oriented title, and adds it to the correct table. Example: `/promote Learn to cook Thai food` |

---

### Focus

Focus lets you tell the bot which goals matter most this week. When focus is set, the morning task generation is weighted toward those goals.

| Command | What it does |
|---|---|
| `/focus` | Show the current weekly focus goals. |
| `/focus <goals>` | Set your weekly focus. Write a short description of what you want to prioritize. Example: `/focus Launch SaaS MVP, Daily exercise habit` |
| `/unfocus` | Clear the focus so all goals are weighted equally again. |

---

### Tasks

| Command | What it does |
|---|---|
| `/tasks` | Show all tasks logged today — scheduled, completed, and failed. |
| `/trigger` | Manually run the daily task generation right now, without waiting for 8 AM. Useful for testing or if you want a mid-day batch. |
| `/done <description>` | Manually mark something as done and log it to the task log. Use this when you complete something yourself rather than through an agent. Example: `/done Researched Agentic AI frameworks` |
| `/update <message>` | Log a progress note to the task log without marking anything complete. Example: `/update Finished research, starting the write-up` |

---

### Coaching

The coaching mode gives you a multi-turn conversation with an LLM coach that knows your goals. It runs on the Mac mini for fast back-and-forth responses.

| Command | What it does |
|---|---|
| `/coach` | Start a coaching session. The LLM reads your current goals and acts as a direct, practical coach. Talk about blockers, decisions, motivation, or anything on your mind. |
| `/endcoach` | End the coaching session and clear the conversation history. |

During a coaching session, any plain text message you send is treated as a message to the coach. Commands still work normally. The conversation history is kept for up to 10 exchanges to give context, then older messages are dropped.

---

## Configuration

Settings are stored in a `.env` file in the project root. Key options:

| Setting | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Your BotFather token |
| `TELEGRAM_ALLOWED_USER_ID` | — | Your Telegram user ID (only you can use the bot) |
| `OLLAMA_MAC_URL` | `http://localhost:11434` | Ollama on the Mac mini |
| `OLLAMA_MAC_MODEL` | `llama3:14b` | Model for light tasks |
| `OLLAMA_DESKTOP_URL` | `http://192.168.50.206:11434` | Ollama on the desktop GPU |
| `OLLAMA_DESKTOP_MODEL` | `deepseek-r1:14b` | Model for heavy tasks |
| `DAILY_TASK_HOUR` | `8` | Hour to run daily task generation (24h) |
| `DAILY_TASK_MINUTE` | `0` | Minute to run daily task generation |
| `TIMEZONE` | `America/New_York` | Timezone for the scheduler |

---

## Updating the Bot

After pulling new code, reinstall and restart:

```bash
cd ~/JacbotCoach
git pull
source .venv/bin/activate
pip install -e .
# Then restart the process (launchd, or kill and re-run jacbotcoach)
```
