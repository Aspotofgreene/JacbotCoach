import logging
import re
from datetime import date
from enum import Enum

from jacbotcoach.config import get_settings
from jacbotcoach.llm.client import OllamaClient, extract_json_list

logger = logging.getLogger(__name__)


def _strip_markdown_emphasis(text: str) -> str:
    """Remove **bold** and *italic* markers so plain-text readers see clean prose."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    # Only strip single-asterisk italics when not a bullet point (line-start `- ` or `* `)
    text = re.sub(r"(?<!\n)\*(?!\s)(.+?)(?<!\s)\*", r"\1", text)
    return text


class Complexity(Enum):
    LIGHT = "light"   # Mac mini: goal parsing, summaries
    HEAVY = "heavy"   # Desktop GPU: task generation, session prompt writing


# Tasks that need the desktop GPU
_HEAVY_TASKS = {"task_generation", "session_prompt"}


class LLMRouter:
    """
    Routes LLM calls between Mac mini (light) and desktop GPU (heavy).

    Routing is static — no meta-LLM call to decide which LLM to use.
    If the desktop is unreachable, all calls fall back to the Mac mini.
    """

    def _client(self, complexity: Complexity) -> OllamaClient:
        settings = get_settings()
        if complexity == Complexity.HEAVY:
            return OllamaClient(settings.ollama_desktop_url, settings.ollama_desktop_model)
        return OllamaClient(settings.ollama_mac_url, settings.ollama_mac_model)

    async def _client_with_fallback(self, complexity: Complexity) -> OllamaClient:
        client = self._client(complexity)
        if complexity == Complexity.HEAVY and not await client.health_check():
            logger.warning(
                "Desktop GPU (%s) unreachable — falling back to Mac mini for heavy task",
                get_settings().ollama_desktop_url,
            )
            return self._client(Complexity.LIGHT)
        return client

    # ------------------------------------------------------------------ #
    # Public methods                                                       #
    # ------------------------------------------------------------------ #

    async def parse_goals(self, raw_goals: str) -> str:
        """
        Light task: convert a raw brain dump into structured AUTONOMOUS.md markdown.
        Runs on Mac mini.
        """
        client = self._client(Complexity.LIGHT)
        prompt = (
            "You are a goal coach and project planner. Convert the following brain dump "
            "into a structured goal plan using EXACTLY this markdown format:\n\n"
            "# Goals\n\n"
            "## Projects — Short Term (1–2 months)\n"
            "| Status | Difficulty | Goal | Notes |\n"
            "|---|---|---|---|\n"
            "| Active | Easy/Medium/Hard | Action-oriented goal title | Brief context |\n\n"
            "## Projects — Long Term (3+ months)\n"
            "| Status | Difficulty | Goal | Notes |\n"
            "|---|---|---|---|\n"
            "| Active | Easy/Medium/Hard | Action-oriented goal title | Brief context |\n\n"
            "## Habits & Ongoing\n"
            "| Status | Frequency | Habit | Why |\n"
            "|---|---|---|---|\n"
            "| Active | Daily/Weekly/Monthly | Habit description | Benefit |\n\n"
            "## Open Backlog\n"
            "- Ideas not yet classified\n\n"
            "## Completed\n"
            "(empty)\n\n"
            "Rules:\n"
            "- Rephrase vague goals into concrete, action-oriented project or habit titles\n"
            "- Classify each goal as a PROJECT (has a clear endpoint) or HABIT (ongoing/recurring)\n"
            "- Short term = can realistically be completed in 1-2 months\n"
            "- Long term = requires 3+ months\n"
            "- Difficulty: Easy (low effort/skill), Medium (moderate), Hard (significant effort/skill)\n"
            "- Status options: Active | In Progress | Paused | Done\n"
            "- Default status for all new goals: Active\n"
            "- Be concise — one row per goal\n"
            "- Total output must be under 48 lines\n"
            "- Output only the markdown, no preamble or explanation\n\n"
            f"Brain dump:\n{raw_goals}"
        )
        return await client.generate(prompt)

    async def weekly_summary(self, goals: str, task_log: str, focus: str = "") -> str:
        """
        Heavy task: generate a weekly accomplishment digest.
        Runs Sunday morning on desktop GPU.
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        week_of = date.today().strftime("%B %d, %Y")
        focus_section = f"\nCurrent focus goals:\n{focus}\n" if focus else ""
        prompt = (
            f"Week ending {week_of}. You are a personal coach writing a weekly review.\n\n"
            f"Goals on file:\n{goals}\n"
            f"{focus_section}"
            f"Task log from this week:\n{task_log}\n\n"
            "Write a concise weekly review covering:\n"
            "1. What was accomplished (2-3 bullet points)\n"
            "2. Which goals made progress and which are stalled\n"
            "3. One honest observation about patterns or momentum\n"
            "4. 2-3 recommended focus areas for next week\n\n"
            "Keep it under 200 words. Be direct and honest, not generic.\n"
            "Output plain text, no markdown headers."
        )
        return await client.generate(prompt, timeout=180.0)

    async def coach_response(self, message: str, goals: str, history: list[dict]) -> str:
        """
        Light task: respond to a free-form coaching message.
        Runs on Mac mini for fast back-and-forth.
        """
        client = self._client(Complexity.LIGHT)
        history_text = "\n".join(
            f"{'You' if m['role'] == 'user' else 'Coach'}: {m['content']}"
            for m in history[-6:]  # last 3 exchanges
        )
        prompt = (
            "You are a direct, practical life and productivity coach. "
            "You know the user's goals and give honest, actionable advice.\n\n"
            f"User's goals:\n{goals}\n\n"
            + (f"Recent conversation:\n{history_text}\n\n" if history_text else "")
            + f"User: {message}\n\n"
            "Coach (respond in 2-4 sentences, be direct and specific):"
        )
        return await client.generate(prompt, timeout=120.0)

    async def classify_backlog_item(self, item: str, goals: str) -> str:
        """
        Light task: suggest which category a backlog item belongs to.
        """
        client = self._client(Complexity.LIGHT)
        prompt = (
            "You are a goal planner. Given this backlog item, suggest how to classify it.\n\n"
            f"Backlog item: {item}\n\n"
            f"Existing goals for context:\n{goals}\n\n"
            "Output a single JSON object with these fields:\n"
            '{"category": "Short Term Projects|Long Term Projects|Habits & Ongoing",'
            ' "difficulty": "Easy|Medium|Hard",'
            ' "rephrased": "Action-oriented title",'
            ' "notes": "Brief context"}\n\n'
            "Output only the JSON, no other text."
        )
        return await client.generate(prompt, timeout=60.0)

    async def generate_daily_tasks(self, autonomous_content: str, count: int = 4) -> list[str]:
        """
        Heavy task: reason over goals to produce concrete executable tasks.
        count is the target number of tasks (smart scheduling passes this).
        Runs on desktop GPU (with Mac mini fallback).
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        today = date.today().strftime("%A, %B %d, %Y")
        prompt = (
            f"Today is {today}. You are an autonomous task planner.\n\n"
            "Given the following goals and backlog:\n"
            f"{autonomous_content}\n\n"
            f"Generate exactly {count} concrete, self-contained tasks that can be "
            "completed today by an AI agent with access to a computer, shell, "
            "web browser, and file system.\n\n"
            "Each task must be:\n"
            "- Specific and actionable (not vague like 'work on goals')\n"
            "- Completable in 1-2 hours\n"
            "- Directly tied to one of the listed goals\n\n"
            "Output ONLY a JSON array of strings, e.g.:\n"
            '["Task one description", "Task two description"]\n\n'
            "No other text."
        )
        response = await client.generate(prompt, timeout=240.0)
        tasks = extract_json_list(response)
        return tasks[:count]

    async def detect_intent(self, message: str, goals: str) -> dict:
        """
        Light task: classify a free-text message into a bot action.
        Returns {"action": str, "args": str, "reply": str}
        Actions: goal_done | goal_status | goal_delete | goal_edit | promote |
                 add_milestone | done_milestone | history | streaks | coach |
                 trigger | tasks | focus | status | unknown
        """
        client = self._client(Complexity.LIGHT)
        prompt = (
            "You are parsing a message from a user of a productivity bot. "
            "Classify the message into one of these actions and extract arguments.\n\n"
            f"User's goals for context:\n{goals[:500]}\n\n"
            f"Message: {message}\n\n"
            "Output a single JSON object:\n"
            '{"action": "<action>", "args": "<extracted arguments>", '
            '"reply": "<short confirmation to show user>"}\n\n'
            "Actions:\n"
            "  goal_done   — user wants to mark a goal complete. args=goal title\n"
            "  goal_status — user wants to update goal status. args='title | status'\n"
            "  goal_delete — user wants to delete a goal. args=goal title\n"
            "  goal_edit   — user wants to rename a goal. args='old | new'\n"
            "  promote     — user wants to promote a backlog item. args=item text\n"
            "  add_milestone — user wants to add a milestone. args='goal | milestone'\n"
            "  done_milestone — user completed a milestone. args='goal | milestone'\n"
            "  history     — user wants history for a goal. args=goal title\n"
            "  streaks     — user asking about habit streaks. args=''\n"
            "  coach       — user wants coaching conversation. args=''\n"
            "  trigger     — user wants to generate tasks now. args=''\n"
            "  tasks       — user wants to see today's tasks. args=''\n"
            "  focus       — user wants to set/see focus. args=focus text or empty\n"
            "  status      — user wants to see goals file. args=''\n"
            "  unknown     — cannot classify. args=''\n\n"
            "Output only the JSON."
        )
        raw = await client.generate(prompt, timeout=60.0)
        try:
            import json, re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            return json.loads(m.group(0)) if m else {"action": "unknown", "args": "", "reply": ""}
        except Exception:
            return {"action": "unknown", "args": "", "reply": ""}

    async def stall_nudge(self, goal: str, days_inactive: int) -> str:
        """Light task: generate a gentle nudge for a stalled goal."""
        client = self._client(Complexity.LIGHT)
        prompt = (
            f"A user has not made progress on their goal for {days_inactive} days:\n"
            f"Goal: {goal}\n\n"
            "Write a single short, warm but direct motivational nudge (1-2 sentences). "
            "Ask if they want to refocus, break it down, or pause it. "
            "Do not be generic. Reference the goal specifically."
        )
        return await client.generate(prompt, timeout=60.0)

    async def match_task_to_goal(self, task: str, goals: str) -> str | None:
        """
        Light task: find which goal a completed task is most related to.
        Returns the goal title string, or None if no clear match.
        """
        client = self._client(Complexity.LIGHT)
        prompt = (
            "Given this completed task, identify which goal it most likely contributes to.\n\n"
            f"Completed task: {task}\n\n"
            f"Goals:\n{goals}\n\n"
            'Output a JSON object: {"goal": "<exact goal title from the list or null>"}\n'
            "If no goal clearly matches, set goal to null.\n"
            "Output only the JSON."
        )
        raw = await client.generate(prompt, timeout=60.0)
        try:
            import json, re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                return data.get("goal")
        except Exception:
            pass
        return None

    async def propose_weekly_plan(self, goals: str, task_log: str, focus: str = "") -> str:
        """
        Heavy task: propose a concrete focus for next week based on this week's activity.
        Runs Sunday evening on desktop GPU (with Mac mini fallback).
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        week_of = date.today().strftime("%B %d, %Y")
        focus_section = f"\nCurrent focus goals:\n{focus}\n" if focus else ""
        prompt = (
            f"Week ending {week_of}. You are a personal coach planning next week.\n\n"
            f"Goals on file:\n{goals}\n"
            f"{focus_section}"
            f"Task log from this week:\n{task_log}\n\n"
            "Based on what was accomplished and what is stalling, propose a clear, "
            "specific focus for next week. The focus must:\n"
            "1. Name 2-3 concrete goals or areas to prioritize\n"
            "2. Be grounded in the active goals above (not generic advice)\n"
            "3. Open with one sentence about what worked well this week\n\n"
            "Keep the total output under 80 words. "
            "Output plain text, no markdown headers."
        )
        return await client.generate(prompt, timeout=180.0)

    async def generate_research(self, topic: str) -> str:
        """
        Heavy task: research a topic and produce a structured markdown summary.
        Returns markdown text — the bot writes it to disk.
        Runs on desktop GPU (with Mac mini fallback).
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        today = date.today().strftime("%B %d, %Y")
        prompt = (
            f"You are a rigorous research assistant. Today is {today}. "
            "Produce a thorough, well-cited research report on the topic below. "
            "Write as if preparing a briefing document for an expert reader. "
            "Output only the report content — no preamble, no meta-commentary.\n\n"
            f"Topic: {topic}\n\n"
            "Structure your report with ALL of these sections:\n\n"
            "# [Topic Title]\n\n"
            "## Executive Summary\n"
            "2-3 paragraph overview of the most important findings and why they matter.\n\n"
            "## Background and Context\n"
            "Historical context, how this topic developed, foundational concepts a reader must understand.\n\n"
            "## Core Concepts and Mechanisms\n"
            "Deep explanation of the key ideas, theories, or processes. Be specific and precise.\n\n"
            "## Current State and Recent Developments\n"
            "What is known as of your training cutoff. Major findings, breakthroughs, debates, or shifts.\n\n"
            "## Key Figures, Works, and Sources\n"
            "List the most important researchers, authors, books, papers, institutions, or tools. "
            "For each, give 1-2 sentences on why they matter. "
            "Format each entry as: Name/Title (Year if known) — description.\n\n"
            "## Critical Analysis\n"
            "What are the open questions, controversies, limitations, or competing schools of thought? "
            "What does the evidence actually support vs. what is speculative?\n\n"
            "## Practical Implications\n"
            "Concrete, specific ways this knowledge can be applied. Avoid generic advice.\n\n"
            "## Recommended Reading and Resources\n"
            "A prioritised list: start with the single best entry point for a newcomer, "
            "then 4-6 deeper sources (books, papers, websites, courses). "
            "For each: Title — Author/Source — one sentence on what it adds.\n\n"
            "Formatting rules:\n"
            "- Use ## for section headings as shown above\n"
            "- Write in flowing prose, not bullet points, except in list sections\n"
            "- Do NOT use **bold** or *italic* markdown — plain text only\n"
            "- Aim for 1500-2500 words total\n"
            "- Be specific: name real people, real works, real dates where you know them\n"
            "- If uncertain about a fact, say so rather than confabulating"
        )
        return _strip_markdown_emphasis(await client.generate(prompt, timeout=600.0))

    async def generate_draft(self, topic: str) -> str:
        """
        Heavy task: write a first-draft document directly.
        Returns markdown text — the bot writes it to disk.
        Runs on desktop GPU (with Mac mini fallback).
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        prompt = (
            "Write a first-draft document on the following topic. "
            "Output only the content — no preamble, no explanation.\n\n"
            f"Topic: {topic}\n\n"
            "Format:\n"
            "- Start with a # title heading\n"
            "- Short introduction paragraph\n"
            "- 3–5 sections with ## headings\n"
            "- Conclusion\n"
            "- Aim for 800–1200 words\n"
            "- Use a thoughtful, literary tone\n"
            "- Do NOT use **bold** or *italic* markdown — write in plain prose\n"
            "- The output will be read as plain text so avoid any special formatting"
        )
        return _strip_markdown_emphasis(await client.generate(prompt, timeout=600.0))

    async def accountability_insight(
        self,
        week_ending: str,
        consistency: int,
        focus_alignment: int,
        momentum: int,
        done_count: int,
        scheduled_count: int,
        focus: str,
        goals: str,
    ) -> str:
        """
        Light task: generate a 2-3 sentence coaching insight for a weekly accountability score.
        Runs on Mac mini.
        """
        client = self._client(Complexity.LIGHT)
        prompt = (
            f"Week ending {week_ending}. A user received the following accountability scores:\n"
            f"  Consistency:      {consistency}/10  ({done_count} tasks done out of {scheduled_count} scheduled)\n"
            f"  Focus Alignment:  {focus_alignment}/10\n"
            f"  Momentum:         {momentum}/10\n\n"
            + (f"This week's focus: {focus}\n\n" if focus else "")
            + f"Active goals:\n{goals[:600]}\n\n"
            "Write 2-3 direct, specific sentences of coaching insight. "
            "Mention the weakest dimension by name and give one concrete suggestion. "
            "Be honest but encouraging. No generic platitudes. Output plain text only."
        )
        return await client.generate(prompt, timeout=60.0)

    async def generate_build_prompt(self, idea: str, goals: str, output_dir: str) -> str:
        """
        Heavy task: write a detailed build prompt for an OpenClaw session.
        The agent will scaffold a working prototype in ~/projects/<slug>/.
        Runs on desktop GPU (with Mac mini fallback).
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        today = date.today().isoformat()
        prompt = (
            "Write a detailed, self-contained prompt for an autonomous AI agent "
            "to scaffold a working software prototype for the following idea.\n\n"
            f"Idea: {idea}\n\n"
            f"User's goals and context:\n{goals}\n\n"
            f"The agent has access to a Mac with shell, browser, file system, "
            "and common developer tools (Python, Node, git, etc.).\n\n"
            "The build prompt must instruct the agent to:\n"
            "1. Analyze the idea and decide on the simplest viable tech stack\n"
            "2. Create the project directory at exactly this path: " + output_dir + "\n"
            "3. Scaffold a working prototype with:\n"
            "   - A clear README.md describing what it does and how to run it\n"
            "   - Runnable entry point (main.py, index.js, etc.)\n"
            "   - Any required dependencies listed (requirements.txt, package.json, etc.)\n"
            "   - At least one working feature demonstrating the core concept\n"
            "4. Keep the implementation minimal but functional — no half-finished stubs\n"
            "5. Tailor the prototype to support the user's active goals where relevant\n"
            f"6. End with this exact line: "
            f"'When done, append a ✅ line to memory/tasks-log.md in exactly this format: "
            f"- [{today}] [DONE] ✅ Project built: {idea}'\n"
            "7. Never edit AUTONOMOUS.md directly.\n\n"
            "Output only the build prompt text."
        )
        return await client.generate(prompt, timeout=240.0)

    async def generate_session_prompt(self, task: str, context: str) -> str:
        """
        Heavy task: write a detailed, self-contained prompt for an OpenClaw session.
        Runs on desktop GPU (with Mac mini fallback).
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        prompt = (
            "Write a detailed, self-contained prompt for an autonomous AI agent "
            "to execute the following task. The agent has access to a Mac running "
            "macOS with shell, file system, web browser, and common developer tools.\n\n"
            f"Task: {task}\n\n"
            f"Goal context:\n{context}\n\n"
            "The prompt must include:\n"
            "1. What to do (step-by-step if needed)\n"
            "2. Clear success criteria\n"
            "3. Where to save any output files\n"
            "4. This exact line at the end: "
            "'When done, append a ✅ line to memory/tasks-log.md. "
            "Never edit AUTONOMOUS.md directly.'\n\n"
            "Output only the prompt text."
        )
        return await client.generate(prompt, timeout=240.0)
