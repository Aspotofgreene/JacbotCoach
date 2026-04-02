import logging
from datetime import date
from enum import Enum

from jacbotcoach.config import get_settings
from jacbotcoach.llm.client import OllamaClient, extract_json_list

logger = logging.getLogger(__name__)


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

    async def generate_daily_tasks(self, autonomous_content: str) -> list[str]:
        """
        Heavy task: reason over goals to produce 4-5 concrete executable tasks.
        Runs on desktop GPU (with Mac mini fallback).
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        today = date.today().strftime("%A, %B %d, %Y")
        prompt = (
            f"Today is {today}. You are an autonomous task planner.\n\n"
            "Given the following goals and backlog:\n"
            f"{autonomous_content}\n\n"
            "Generate exactly 4-5 concrete, self-contained tasks that can be "
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
        return tasks[:5]  # cap at 5

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
