from pathlib import Path


class FocusStore:
    """
    Stores the user's weekly focus goals in focus.md.
    Kept separate from AUTONOMOUS.md so it stays token-light.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8").strip()

    def set(self, focus_text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(focus_text.strip() + "\n", encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def is_empty(self) -> bool:
        return not self.read()
