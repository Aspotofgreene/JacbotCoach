from pathlib import Path

AUTONOMOUS_MAX_LINES = 50


class AutonomousStore:
    """
    Manages AUTONOMOUS.md — the shared goals + backlog file.

    Rules:
    - Only the main session (JacbotCoach bot) writes here.
    - Sub-agents (OpenClaw sessions) NEVER touch this file.
    - Hard cap of AUTONOMOUS_MAX_LINES to keep it token-light.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    def write(self, content: str) -> None:
        lines = content.strip().splitlines()
        if len(lines) > AUTONOMOUS_MAX_LINES:
            raise ValueError(
                f"AUTONOMOUS.md would be {len(lines)} lines "
                f"(max {AUTONOMOUS_MAX_LINES}). Summarize before saving."
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content.strip() + "\n", encoding="utf-8")

    def line_count(self) -> int:
        return len(self.read().splitlines())

    def is_empty(self) -> bool:
        content = self.read().strip()
        return not content or "(empty" in content
