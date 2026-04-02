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
            "You are a goal organizer. Convert the following brain dump into "
            "clean, structured markdown under these headings:\n"
            "# Goals\n## Active Goals\n## Open Backlog\n\n"
            "Rules:\n"
            "- Each goal on its own bullet point\n"
            "- Be concise — one line per goal\n"
            "- Total output must be under 45 lines\n"
            "- Output only the markdown, no preamble or explanation\n\n"
            f"Brain dump:\n{raw_goals}"
        )
        return await client.generate(prompt)

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
