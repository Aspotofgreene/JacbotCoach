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

    async def generate_draft_prompt(self, topic: str, goals: str, output_path: str) -> str:
        """
        Heavy task: write a detailed drafting prompt for an OpenClaw session.
        The agent will produce a first-draft markdown document on the topic.
        Runs on desktop GPU (with Mac mini fallback).
        """
        client = await self._client_with_fallback(Complexity.HEAVY)
        today = date.today().isoformat()
        prompt = (
            "Write a detailed, self-contained prompt for an autonomous AI agent "
            "to produce a first-draft piece of writing on the following topic.\n\n"
            f"Topic: {topic}\n\n"
            f"User's goals and context:\n{goals}\n\n"
            f"The agent has access to a Mac with shell, browser, and file system.\n\n"
            "The drafting prompt must instruct the agent to:\n"
            "1. Research the topic briefly (web search or existing files) for supporting details\n"
            "2. Write a substantial first draft (800–2000 words) in clear, engaging prose\n"
            "3. Structure it with a title, introduction, 3-5 body sections, and a conclusion\n"
            "4. Tailor the tone and depth to support the user's goal (e.g. book writing, articles)\n"
            f"5. Save the draft as a markdown file at exactly this path: {output_path}\n"
            "6. The file must start with a # heading matching the topic\n"
            f"7. End with this exact line: "
            f"'When done, append a ✅ line to memory/tasks-log.md in exactly this format: "
            f"- [{today}] [DONE] ✅ Draft complete: {topic}'\n"
            "8. Never edit AUTONOMOUS.md directly.\n\n"
            "Output only the drafting prompt text."
        )
        return await client.generate(prompt, timeout=240.0)

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
