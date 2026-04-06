import re


def fix_md(text: str) -> str:
    """Normalize LLM markdown output for Telegram's parse_mode="Markdown".

    Converts double-asterisk bold (**text**) to single-asterisk bold (*text*),
    which is what Telegram's Markdown parser expects.
    """
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text, flags=re.DOTALL)
