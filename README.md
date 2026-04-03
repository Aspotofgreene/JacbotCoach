# JacbotCoach

A personal productivity bot that runs on your Mac mini and connects to your Telegram account. You send it your goals, and it autonomously generates daily tasks, dispatches them to AI agents for execution, and keeps you updated throughout the day.

---

## How It Works

JacbotCoach is made up of three layers working together:

1. **You (Telegram)** — You interact with the bot entirely through Telegram commands. You set goals, check on tasks, log progress, and have coaching conversations.

2. **JacbotCoach (Mac mini)** — The bot process runs on your Mac mini. It reads your goals, talks to the LLMs, manages your goal file, and drives the daily schedule. It uses two Ollama instances for AI:
   - **Mac mini** (`llama3:14b`) — fast, used for light tasks like coaching replies, goal classification, and parsing
   - **Desktop GPU** (`deepseek-r1:14b`) — used for heavy tasks like generating daily tasks and writing detailed agent prompts. Falls back to the Mac mini if unreachable.

3. **OpenClaw agents** — When a task is ready to run, JacbotCoach spawns an OpenClaw session on the Mac mini. OpenClaw is an AI agent with access to your Mac's shell, file system, web browser, and developer tools. It executes the task autonomously and writes its results back to the task log. When it finishes, JacbotCoach notifies you on Telegram.

### Daily Routine

Every morning at 8 AM (configurable), JacbotCoach:

1. Reads your goals from `AUTONOMOUS.md`
2. Reads your weekly focus (if set) and weights tasks toward your priorities
3. Sends your goals to the desktop GPU to generate 4–5 concrete, executable tasks
4. For each task, generates a detailed self-contained prompt for an OpenClaw agent
5. Spawns one OpenClaw session per task
6. Logs each task to `memory/tasks-log.md`
7. Sends you a Telegram summary of what was queued

Throughout the day, a background watcher polls the task log every 30 seconds. When OpenClaw finishes a task, you receive a Telegram notification automatically.

Every Sunday at 9 AM, a weekly summary is generated reviewing what was accomplished, which goals made progress, and what to focus on next.

### File Storage

| File | Purpose |
|---|---|
| `AUTONOMOUS.md` | Your goals, habits, backlog, and completed archive. Hard capped at 50 lines. Only JacbotCoach writes here — OpenClaw agents never touch it. |
| `memory/tasks-log.md` | Append-only log of every task. OpenClaw agents append here when they finish work. |
| `focus.md` | Your current weekly focus goals. Influences what tasks get generated each morning. |

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
# Clone the repo
git clone https://github.com/Aspotofgreene/JacbotCoach.git
cd JacbotCoach

# Create a Python 3.11 virtual environment
/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv
source .venv/bin/activate

# Install
pip install --upgrade pip
pip install -e .

# Configure
cp .env.example .env
# Edit .env with your tokens and addresses
```

### Pull the required Ollama models

On the **Mac mini**:
```bash
ollama pull llama3:14b
```

On the **desktop**:
```bash
ollama pull deepseek-r1:14b
```

### Run

```bash
source .venv/bin/activate
jacbotcoach
```

On startup, the bot checks connectivity to both Ollama instances and OpenClaw and logs the results. Then send `/start` to your bot on Telegram.

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
    <string>sleep 15 &amp;&amp; exec /path/to/JacbotCoach/.venv/bin/jacbotcoach</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/path/to/JacbotCoach</string>
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

The 15-second delay ensures the network is available before the bot tries to connect to Telegram. Replace `/path/to/JacbotCoach` with your actual path.

Then load it:
```bash
launchctl load ~/Library/LaunchAgents/com.jacbotcoach.plist
```

### Updating

```bash
cd ~/JacbotCoach
git pull
source .venv/bin/activate
pip install -e .
# Restart the process
```

---

## Commands

### Getting Started

| Command | Description |
|---|---|
| `/start` | Show the full command reference |

### Goals

| Command | Description |
|---|---|
| `/goals` | Brain dump your goals in plain language. The LLM parses, categorizes, and structures them automatically. |
| `/goals_list` | View all goals as a flat numbered list. |
| `/goals_cat` | View goals grouped by category. |
| `/goals_reparse` | Re-run AI structuring on the current goals file. Use this if goals were saved as raw text instead of a structured table (e.g. if Ollama was unreachable when you first saved them). |
| `/status` | View the raw `AUTONOMOUS.md` file with line count. |

### Managing Individual Goals

| Command | Description |
|---|---|
| `/goal_done <title>` | Mark a goal complete and archive it. Example: `/goal_done Launch SaaS MVP` |
| `/goal_status <title> <status>` | Update a goal's status: `Active`, `In Progress`, `Paused`, or `Done`. Example: `/goal_status Launch SaaS MVP In Progress` |
| `/goal_edit <old> \| <new>` | Rename a goal. Example: `/goal_edit Learn Spanish \| Learn Portuguese` |
| `/goal_delete <title>` | Permanently remove a goal. Example: `/goal_delete Learn Spanish` |
| `/promote <item>` | Move a backlog item into active goals. The LLM classifies the category, difficulty, and rewrites it as an action-oriented title. |

### Focus

| Command | Description |
|---|---|
| `/focus` | Show current weekly focus goals. |
| `/focus <goals>` | Set weekly focus. Morning task generation will prioritize these. Example: `/focus Launch SaaS MVP, Daily exercise` |
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
| `/coach` | Start a multi-turn coaching conversation. The LLM reads your goals and responds as a direct, practical coach. |
| `/endcoach` | End the coaching session. |

During a coaching session, any plain text message you send goes to the coach. The conversation history is kept for up to 10 exchanges for context.

---

## Troubleshooting

**Bot is running but not responding to messages**
- Check that `TELEGRAM_ALLOWED_USER_ID` in `.env` matches your actual Telegram ID (get it from [@userinfobot](https://t.me/userinfobot))
- Make sure only one instance is running: `pgrep -fl jacbotcoach` should show exactly one PID
- If you see multiple PIDs: `pkill -f jacbotcoach && sleep 2 && launchctl start com.jacbotcoach`

**Goals saved as raw text instead of structured tables**
- Run `/goals_reparse` — it re-sends the existing content through the LLM structuring step

**Bot fails to start on boot**
- Usually caused by the bot starting before the network is ready
- Add a `sleep 15` delay in the launchd plist (see Autostart section above)

---

## Configuration

All settings are in `.env`. Copy `.env.example` to get started.

| Setting | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Your BotFather token |
| `TELEGRAM_ALLOWED_USER_ID` | — | Your Telegram user ID (only you can use the bot) |
| `OLLAMA_MAC_URL` | `http://localhost:11434` | Ollama on the Mac mini |
| `OLLAMA_MAC_MODEL` | `llama3:14b` | Model for light tasks |
| `OLLAMA_DESKTOP_URL` | `http://192.168.50.206:11434` | Ollama on the desktop GPU |
| `OLLAMA_DESKTOP_MODEL` | `deepseek-r1:14b` | Model for heavy tasks |
| `DAILY_TASK_HOUR` | `8` | Hour to run daily task generation (24h) |
| `DAILY_TASK_MINUTE` | `0` | Minute offset |
| `TIMEZONE` | `America/New_York` | Scheduler timezone |
| `AUTONOMOUS_MD_PATH` | `AUTONOMOUS.md` | Path to goals file |
| `TASKS_LOG_PATH` | `memory/tasks-log.md` | Path to task log |
